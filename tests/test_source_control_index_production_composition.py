from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from agent.services.knowledge_index_job_service import KnowledgeIndexJobService
from agent.services.source_access_enforcement import (
    ResolvedSourceGrant,
    source_access_grant_digest,
)
from agent.services.source_access_manifest_signing import (
    HubSourceAccessManifestSigner,
    SourceAccessSigningKey,
)
from agent.services.source_control_index_authority_planner import (
    BoundSourceIndexAuthority,
    BoundSourceRevisionAuthority,
    BoundSourceRevisionAuthorityPlanner,
    BoundSourceRevisionPayload,
    BoundSourceRevisionPlanningError,
)
from agent.services.source_control_index_production_wiring import (
    IngestionKnowledgeIndexPayloadStore,
)
from agent.services.source_destination_resolution import (
    DestinationCatalogRecord,
    DestinationSelection,
    SourceDestinationResolutionService,
)
from agent.services.strict_source_control_knowledge_index_composition import (
    StrictGovernedKnowledgeIndexDependencies,
    StrictKnowledgeIndexCompositionError,
    build_strict_governed_knowledge_index_job_service,
)
from ananta_contracts.knowledge_index_execution import (
    KnowledgeIndexExecutionAssignment,
    KnowledgeIndexResourceBudget,
)
from ananta_contracts.source_control import (
    GrantOperation,
    GrantState,
    GrantTransformation,
    ProviderLocation,
    SourceAccessGrant,
)
from worker.retrieval.governed_knowledge_index_worker_composition import (
    GovernedKnowledgeIndexWorkerSecurity,
    build_governed_knowledge_index_worker_handler,
)

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def test_legacy_ingestion_payload_store_keeps_keyword_constructor() -> None:
    content = b'{"records":[]}'
    fingerprint = hashlib.sha256(content).hexdigest()
    artifact = SimpleNamespace(
        id="artifact-legacy-payload",
        artifact_metadata={"legacy": True},
    )
    version = SimpleNamespace(
        sha256=fingerprint,
        size_bytes=len(content),
        media_type=(
            "application/vnd.ananta.knowledge-index-job+json"
        ),
    )

    class Ingestion:
        def upload_artifact(self, **kwargs):
            assert kwargs["content"] == content
            assert kwargs["artifact_metadata"] == {
                "system_artifact_kind": (
                    "knowledge_index_job_payload"
                ),
                "idempotency_fingerprint": fingerprint,
            }
            return artifact, version, None

    class Artifacts:
        saved = None

        def save(self, value):
            self.saved = value

    artifacts = Artifacts()
    reference = IngestionKnowledgeIndexPayloadStore(
        ingestion=Ingestion(),
        artifact_repository=artifacts,
    ).store_payload(
        content=content,
        fingerprint=fingerprint,
        created_by="legacy-caller",
    )

    assert reference == {
        "artifact_id": artifact.id,
        "sha256": fingerprint,
        "size_bytes": len(content),
        "media_type": version.media_type,
    }
    assert artifacts.saved is artifact
    assert artifact.artifact_metadata == {
        "legacy": True,
        "system_artifact_kind": "knowledge_index_job_payload",
        "idempotency_fingerprint": fingerprint,
    }


class _DestinationCatalog:
    def __init__(self, record: DestinationCatalogRecord) -> None:
        self.record = record

    def resolve(self, **kwargs):
        expected = {
            "worker_id": self.record.worker_id,
            "runtime_id": self.record.runtime_id,
            "provider_id": self.record.provider_id,
            "model_id": self.record.model_id,
        }
        return self.record if kwargs == expected else None


class _RevisionPort:
    def __init__(self, revision: BoundSourceRevisionAuthority) -> None:
        self.revision = revision

    def resolve_bound_revision(self, **kwargs):
        expected = {
            "tenant_id": self.revision.tenant_id,
            "project_id": self.revision.project_id,
            "connection_id": self.revision.connection_id,
            "source_revision_id": self.revision.source_revision_id,
        }
        return self.revision if kwargs == expected else None


class _PayloadPort:
    def __init__(self, payload: BoundSourceRevisionPayload) -> None:
        self.payload = payload

    def load_bound_revision_payload(self, revision):
        assert revision.source_revision_id == self.payload.source_revision_id
        return self.payload


class _AuthorityPort:
    def __init__(self, authority: BoundSourceIndexAuthority) -> None:
        self.authority = authority

    def resolve_bound_index_authority(self, **kwargs):
        assert kwargs["revision"].admission_state == "admitted"
        assert kwargs["actor_id"] == "actor-example"
        assert kwargs["idempotency_key"] == "index-example"
        return self.authority


class _GrantPort:
    def __init__(self, grant: SourceAccessGrant | None) -> None:
        self.grant = grant
        self.request = None

    def resolve_active(self, request):
        self.request = request
        if self.grant is None:
            return None
        return ResolvedSourceGrant(
            grant=self.grant,
            consumption_mode="reusable",
            concurrency_version=1,
        )


def _planner_fixture(
    *,
    grant_lifetime: timedelta = timedelta(hours=1),
    lease_lifetime: timedelta = timedelta(minutes=8),
):
    revision = BoundSourceRevisionAuthority(
        tenant_id="tenant-example",
        project_id="project-example",
        connection_id=f"conn_{'a' * 64}",
        source_revision_id=f"srev_{'b' * 64}",
        source_revision_digest="c" * 64,
        content_manifest_digest="d" * 64,
        connector_type="registered_workspace",
        source_id="source-example",
        admission_state="admitted",
        admission_digest="e" * 64,
    )
    payload = BoundSourceRevisionPayload(
        source_revision_id=revision.source_revision_id,
        source_revision_digest=revision.source_revision_digest,
        content_manifest_digest=revision.content_manifest_digest,
        files=(
            {
                "relative_path": "src/main.ts",
                "sha256": "f" * 64,
                "size_bytes": 12,
            },
        ),
        records=(
            {
                "id": "src/main.ts",
                "content": "export {};",
                "metadata": {"relative_path": "src/main.ts"},
            },
        ),
    )
    selection = DestinationSelection(
        worker_id="worker_example",
        runtime_id="runtime_example",
        provider_id="provider_example",
        model_id="model_example",
    )
    catalog = _DestinationCatalog(
        DestinationCatalogRecord(
            worker_id=selection.worker_id,
            worker_kind="retrieval",
            runtime_id=selection.runtime_id,
            runtime_kind="worker",
            provider_id=selection.provider_id,
            model_id=selection.model_id,
            model_class="embedding",
            provider_location=ProviderLocation.LOCAL_CONTAINER,
            data_residency="local",
            enabled=True,
            authorization_status="authorized",
        )
    )
    destinations = SourceDestinationResolutionService(catalog)
    resolved_destination = destinations.resolve(selection)
    grant = SourceAccessGrant.create(
        version=1,
        tenant_id=revision.tenant_id,
        project_id=revision.project_id,
        source_revision_id=revision.source_revision_id,
        destination_id=resolved_destination.descriptor.destination_id,
        operation=GrantOperation.INDEX,
        transformation=GrantTransformation.REDACTED,
        purpose="knowledge-index",
        policy_version="policy-example-v1",
        policy_snapshot_digest="1" * 64,
        state=GrantState.ACTIVE,
        issued_at=NOW - timedelta(minutes=1),
        expires_at=NOW + grant_lifetime,
    )
    authority = BoundSourceIndexAuthority(
        policy_snapshot_id="policy-example-v1",
        policy_snapshot_digest="1" * 64,
        destination_selection=selection,
        source_access_grant_id=grant.grant_id,
        source_access_grant_digest=source_access_grant_digest(grant),
        resources=KnowledgeIndexResourceBudget(
            max_files=100,
            max_total_bytes=1024 * 1024,
            max_file_bytes=128 * 1024,
            max_runtime_seconds=300,
            max_memory_bytes=128 * 1024 * 1024,
            max_output_bytes=1024 * 1024,
        ),
        assignment=KnowledgeIndexExecutionAssignment(
            assignment_id="assignment_example",
            worker_id=selection.worker_id,
            lease_id="lease_example",
            lease_generation=1,
            lease_issued_epoch_ms=int((NOW - timedelta(seconds=1)).timestamp() * 1000),
            lease_expires_epoch_ms=int(
                (NOW + lease_lifetime).timestamp() * 1000
            ),
        ),
    )
    grants = _GrantPort(grant)
    planner = BoundSourceRevisionAuthorityPlanner(
        revisions=_RevisionPort(revision),
        payloads=_PayloadPort(payload),
        authority=_AuthorityPort(authority),
        destinations=destinations,
        grants=grants,
        clock=lambda: NOW,
    )
    return planner, revision, grants


def test_planner_builds_only_a_current_revision_bound_plan() -> None:
    planner, revision, grants = _planner_fixture()

    plan = planner.plan_bound_source_revision(
        tenant_id=revision.tenant_id,
        project_id=revision.project_id,
        actor_id="actor-example",
        connection_id=revision.connection_id,
        source_revision_id=revision.source_revision_id,
        source_revision_digest=revision.source_revision_digest,
        content_manifest_digest=revision.content_manifest_digest,
        descriptor={
            "source_id": revision.source_id,
            "tenant_id": revision.tenant_id,
            "project_id": revision.project_id,
        },
        idempotency_key="index-example",
    )

    assert set(plan) == {
        "hub_task_id",
        "source_revision_id",
        "source_revision_digest",
        "admission_digest",
        "policy_snapshot_id",
        "policy_snapshot_digest",
        "destination_id",
        "destination_digest",
        "source_access_grant_id",
        "source_access_grant_digest",
        "files",
        "resource_budget",
        "assignment",
        "destination_selection",
        "source_scope",
        "source_id",
        "records",
    }
    assert plan["source_revision_id"] == revision.source_revision_id
    assert plan["admission_digest"] == revision.admission_digest
    assert grants.request.source_revision_digest == revision.source_revision_digest
    assert grants.request.operation is GrantOperation.INDEX


def test_planner_rejects_a_stale_revision_before_grant_resolution() -> None:
    planner, revision, grants = _planner_fixture()

    with pytest.raises(BoundSourceRevisionPlanningError) as raised:
        planner.plan_bound_source_revision(
            tenant_id=revision.tenant_id,
            project_id=revision.project_id,
            actor_id="actor-example",
            connection_id=revision.connection_id,
            source_revision_id=revision.source_revision_id,
            source_revision_digest="0" * 64,
            content_manifest_digest=revision.content_manifest_digest,
            descriptor={"source_id": revision.source_id},
            idempotency_key="index-example",
        )

    assert raised.value.reason_code == "source_revision_stale"
    assert grants.request is None


def test_planner_requires_runtime_and_transport_margin_on_assignment() -> None:
    planner, revision, _grants = _planner_fixture(
        lease_lifetime=timedelta(minutes=5),
    )

    with pytest.raises(BoundSourceRevisionPlanningError) as raised:
        planner.plan_bound_source_revision(
            tenant_id=revision.tenant_id,
            project_id=revision.project_id,
            actor_id="actor-example",
            connection_id=revision.connection_id,
            source_revision_id=revision.source_revision_id,
            source_revision_digest=revision.source_revision_digest,
            content_manifest_digest=revision.content_manifest_digest,
            descriptor={"source_id": revision.source_id},
            idempotency_key="index-example",
        )

    assert raised.value.reason_code == (
        "source_index_assignment_runtime_window_insufficient"
    )


def test_planner_rejects_grant_without_runtime_transport_margin() -> None:
    planner, revision, _grants = _planner_fixture(
        grant_lifetime=timedelta(minutes=5),
    )

    with pytest.raises(BoundSourceRevisionPlanningError) as raised:
        planner.plan_bound_source_revision(
            tenant_id=revision.tenant_id,
            project_id=revision.project_id,
            actor_id="actor-example",
            connection_id=revision.connection_id,
            source_revision_id=revision.source_revision_id,
            source_revision_digest=revision.source_revision_digest,
            content_manifest_digest=revision.content_manifest_digest,
            descriptor={"source_id": revision.source_id},
            idempotency_key="index-example",
        )

    assert raised.value.reason_code == (
        "source_index_grant_runtime_window_insufficient"
    )


class _Queue:
    def ingest_task(self, **kwargs):
        return kwargs


class _PayloadStore:
    def prepare_reference(self, **kwargs):
        return kwargs

    def store_payload(self, **kwargs):
        return kwargs


class _ExecutionBindings:
    def prepare_issue(self, **kwargs):
        return kwargs

    def validate_prepared_issue(self, record):
        return record

    def admit_prepared_issue(self, record):
        return record

    def get_by_idempotency(self, **kwargs):
        return None

    def same_submission(self, existing, candidate):
        return existing == candidate

    def issue(self, **kwargs):
        return kwargs


class _WorkerDirectory:
    def resolve_worker_url(self, worker_id):
        assert worker_id == "worker_example"
        return "http://worker-example:5001"


def test_strict_hub_composition_requires_every_persistent_dependency() -> None:
    catalog = _DestinationCatalog(
        DestinationCatalogRecord(
            worker_id="worker_example",
            worker_kind="retrieval",
            runtime_id="runtime_example",
            runtime_kind="worker",
            provider_id="provider_example",
            model_id="model_example",
            model_class="embedding",
            provider_location=ProviderLocation.LOCAL_CONTAINER,
            data_residency="local",
            enabled=True,
            authorization_status="authorized",
        )
    )
    dependencies = StrictGovernedKnowledgeIndexDependencies(
        destination_catalog=catalog,
        source_control_engine=object(),
        signing_key=SourceAccessSigningKey("key-v1", b"k" * 32),
        execution_binding_service=_ExecutionBindings(),
        task_queue=_Queue(),
        task_repository=object(),
        payload_store=_PayloadStore(),
        worker_artifact_service=object(),
        source_control_completion_projector=object(),
        worker_directory=_WorkerDirectory(),
    )

    service = build_strict_governed_knowledge_index_job_service(dependencies)

    assert isinstance(service, KnowledgeIndexJobService)
    with pytest.raises(StrictKnowledgeIndexCompositionError):
        StrictGovernedKnowledgeIndexDependencies(
            destination_catalog=catalog,
            source_control_engine=object(),
            signing_key=SourceAccessSigningKey("key-v1", b"k" * 32),
            execution_binding_service=_ExecutionBindings(),
            task_queue=_Queue(),
            task_repository=object(),
            payload_store=None,
            worker_artifact_service=object(),
            source_control_completion_projector=object(),
            worker_directory=_WorkerDirectory(),
        )


def test_worker_composition_requires_identity_and_signed_v2_manifests() -> None:
    key = SourceAccessSigningKey("key-v1", b"s" * 32)
    security = GovernedKnowledgeIndexWorkerSecurity(
        worker_id="worker_example",
        verification_keys={key.key_id: key.secret},
    )

    handler = build_governed_knowledge_index_worker_handler(
        security=security,
        index_service=object(),
    )

    digest = "a" * 64
    signature = HubSourceAccessManifestSigner(key).sign(
        manifest_digest=digest
    )
    assert handler._worker_id == "worker_example"
    assert handler._source_access_manifest_verifier.verify(
        manifest_digest=digest,
        signature=signature,
    )
    assert handler._allow_legacy_unsigned_source_dispatch is False
