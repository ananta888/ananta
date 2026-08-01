from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from agent.services.knowledge_index_job_service import (
    KNOWLEDGE_INDEX_EXECUTION_JOB_SCHEMA,
    KnowledgeIndexJobService,
)
from agent.services.source_access_enforcement import (
    ResolvedSourceGrant,
    SourceAccessEnforcementService,
    source_access_grant_digest,
)
from agent.services.source_access_manifest_signing import (
    HubSourceAccessManifestSigner,
    SourceAccessSigningKey,
)
from agent.services.source_destination_resolution import (
    DestinationCatalogRecord,
    DestinationResolutionError,
    DestinationSelection,
    SourceDestinationResolutionService,
)
from ananta_contracts.source_control import (
    GrantOperation,
    GrantState,
    GrantTransformation,
    ProviderLocation,
    SourceAccessGrant,
)


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


class _Catalog:
    def __init__(self, records):
        self.records = tuple(records)

    def resolve(self, **coordinates):
        return next(
            (
                record
                for record in self.records
                if all(
                    getattr(record, key) == value
                    for key, value in coordinates.items()
                )
            ),
            None,
        )


class _Repository:
    def __init__(self, task):
        self.task = task

    def get_by_id(self, task_id):
        return self.task if task_id == self.task["id"] else None

    def save(self, task):
        self.task = task


class _BoundJob:
    def __init__(self, envelope):
        self.envelope = envelope

    def to_wire(self):
        return dict(self.envelope)


class _BindingGate:
    def __init__(self, envelope):
        self.envelope = envelope

    def validate_before_dispatch(self, **_kwargs):
        return SimpleNamespace(job=_BoundJob(self.envelope))


class _GrantResolver:
    def __init__(self, grant):
        self.grant = grant

    def resolve_active(self, _request):
        return ResolvedSourceGrant(
            grant=self.grant,
            consumption_mode="one_time",
            concurrency_version=1,
        )


class _Consumptions:
    def __init__(self):
        self.calls = []

    def consume_once(self, **kwargs):
        self.calls.append(kwargs)
        return len(self.calls) == 1


def _record(model_id):
    return DestinationCatalogRecord(
        worker_id="worker-index-01",
        worker_kind="llm",
        runtime_id="runtime-index-01",
        runtime_kind="docker_container",
        provider_id="ollama-local",
        model_id=model_id,
        model_class="embedding_model",
        provider_location=ProviderLocation.LOCAL_CONTAINER,
        data_residency="local",
        enabled=True,
        authorization_status="authorized",
    )


def _selection(model_id):
    return DestinationSelection(
        worker_id="worker-index-01",
        runtime_id="runtime-index-01",
        provider_id="ollama-local",
        model_id=model_id,
    )


def _composition():
    destinations = SourceDestinationResolutionService(
        _Catalog([_record("model-a"), _record("model-b")])
    )
    preview = destinations.resolve(_selection("model-a"))
    grant = SourceAccessGrant.create(
        version=1,
        tenant_id="tenant-alpha",
        project_id="project-atlas",
        source_revision_id="srev_" + "a" * 64,
        destination_id=preview.descriptor.destination_id,
        operation=GrantOperation.INDEX,
        transformation=GrantTransformation.REDACTED,
        purpose="knowledge-index",
        policy_version="policy-snapshot-v7",
        state=GrantState.ACTIVE,
        issued_at=NOW - timedelta(minutes=1),
        expires_at=NOW + timedelta(minutes=5),
    )
    envelope = {
        "schema": KNOWLEDGE_INDEX_EXECUTION_JOB_SCHEMA,
        "job_id": "knowledge-index-bound-destination",
        "authority_binding": {
            "tenant_id": "tenant-alpha",
            "project_id": "project-atlas",
            "source_revision_id": "srev_" + "a" * 64,
            "source_revision_digest": "b" * 64,
            "policy_snapshot_id": "policy-snapshot-v7",
            "policy_snapshot_digest": "c" * 64,
            "destination_id": preview.descriptor.destination_id,
            "destination_digest": preview.destination_digest,
            "source_access_grant_id": grant.grant_id,
            "source_access_grant_digest": (
                source_access_grant_digest(grant)
            ),
        },
        "assignment": {
            "assignment_id": "assignment-index-01",
            "worker_id": "worker-index-01",
            "lease_id": "lease-index-01",
            "lease_generation": 1,
            "lease_issued_epoch_ms": 1_000,
            "lease_expires_epoch_ms": 20_000,
        },
        "file_manifest": {
            "manifest_id": "manifest_" + "d" * 64,
            "manifest_digest": "d" * 64,
        },
    }
    task = {
        "id": envelope["job_id"],
        "status": "todo",
        "worker_execution_context": {
            "knowledge_index_job": envelope,
            "source_access_intent": {
                "operation": "index",
                "transformation": "redacted",
                "purpose": "knowledge-index",
                "policy_version": "policy-snapshot-v7",
            },
        },
    }
    consumptions = _Consumptions()
    enforcement = SourceAccessEnforcementService(
        grants=_GrantResolver(grant),
        consumptions=consumptions,
        signer=HubSourceAccessManifestSigner(
            SourceAccessSigningKey(
                key_id="source-access-test",
                secret=b"s" * 32,
            )
        ),
    )
    service = KnowledgeIndexJobService(
        task_repository=_Repository(task),
        execution_binding_service=_BindingGate(envelope),
        destination_resolution_service=destinations,
        source_access_enforcement_service=enforcement,
        clock=lambda: NOW.timestamp(),
    )
    return service, consumptions


def test_preview_destination_is_re_resolved_and_bound_at_dispatch() -> None:
    service, consumptions = _composition()

    context = service.authorize_bound_worker_dispatch(
        job_id="knowledge-index-bound-destination",
        authenticated_worker_id="worker-index-01",
        destination_selection=_selection("model-a").__dict__,
    )

    manifest = context["knowledge_index_job"][
        "source_access_enforcement_manifest"
    ]
    assert manifest["destination_digest"] == (
        context["knowledge_index_job"]["authority_binding"][
            "destination_digest"
        ]
    )
    assert manifest["assignment_id"] == "assignment-index-01"
    assert manifest["lease_id"] == "lease-index-01"
    assert manifest["content_manifest_id"] == (
        context["knowledge_index_job"]["file_manifest"]["manifest_id"]
    )
    assert len(consumptions.calls) == 1


def test_model_change_after_preview_blocks_before_grant_consumption() -> None:
    service, consumptions = _composition()

    with pytest.raises(
        DestinationResolutionError,
        match="destination_changed_after_preview",
    ):
        service.authorize_bound_worker_dispatch(
            job_id="knowledge-index-bound-destination",
            authenticated_worker_id="worker-index-01",
            destination_selection=_selection("model-b").__dict__,
        )

    assert consumptions.calls == []
