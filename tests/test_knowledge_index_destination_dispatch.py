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
    WorkerSourceAccessManifestVerifier,
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
        self.fail_next_save = False
        self.replace_calls = []

    def get_by_id(self, task_id):
        return self.task if task_id == self.task["id"] else None

    def save(self, task):
        if self.fail_next_save:
            self.fail_next_save = False
            raise RuntimeError("simulated_task_envelope_save_failure")
        self.task = task

    def replace_bound_knowledge_index_envelope(
        self,
        task_id,
        *,
        expected_envelope,
        replacement_envelope,
    ):
        self.replace_calls.append(
            {
                "task_id": task_id,
                "expected_envelope": expected_envelope,
                "replacement_envelope": replacement_envelope,
            }
        )
        assert task_id == self.task["id"]
        context = dict(self.task["worker_execution_context"])
        assert context["knowledge_index_job"] == expected_envelope
        if self.fail_next_save:
            self.fail_next_save = False
            raise RuntimeError("simulated_task_envelope_save_failure")
        context["knowledge_index_job"] = dict(replacement_envelope)
        self.task = {**self.task, "worker_execution_context": context}


class _BoundJob:
    def __init__(self, envelope):
        self.envelope = envelope

    def to_wire(self):
        return dict(self.envelope)


class _BindingGate:
    def __init__(self, envelope):
        self.envelope = envelope
        self.claim_calls = []

    def validate_before_dispatch(self, **_kwargs):
        return SimpleNamespace(
            job=_BoundJob(self.envelope),
            lock_version=1,
        )

    def claim_dispatch(self, **values):
        self.claim_calls.append(values)


class _GrantResolver:
    def __init__(self, grant, consumptions):
        self.grant = grant
        self.consumptions = consumptions

    def resolve_active(self, _request):
        return ResolvedSourceGrant(
            grant=self.grant,
            consumption_mode="one_time",
            concurrency_version=self.consumptions.policy_version,
        )


class _Consumptions:
    def __init__(self):
        self.calls = []
        self.receipt_calls = []
        self.policy_version = 1
        self.consumption_digest = None

    def consume_once(self, **kwargs):
        self.calls.append(kwargs)
        if self.consumption_digest is not None:
            return False
        if kwargs["expected_version"] != self.policy_version:
            return False
        self.consumption_digest = kwargs["consumption_digest"]
        self.policy_version += 1
        return True

    def verify_exact_consumption_receipt(self, **kwargs):
        self.receipt_calls.append(kwargs)
        return bool(
            kwargs["expected_policy_version"] == self.policy_version
            and kwargs["consumption_digest"]
            == self.consumption_digest
        )


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
            "lease_expires_epoch_ms": int(
                (NOW + timedelta(minutes=4)).timestamp() * 1000
            ),
        },
        "resources": {"max_runtime_seconds": 60},
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
    signing_key = SourceAccessSigningKey(
        key_id="source-access-test",
        secret=b"s" * 32,
    )
    grant_resolver = _GrantResolver(grant, consumptions)
    enforcement = SourceAccessEnforcementService(
        grants=grant_resolver,
        consumptions=consumptions,
        signer=HubSourceAccessManifestSigner(signing_key),
        manifest_verifier=WorkerSourceAccessManifestVerifier(
            {signing_key.key_id: signing_key.secret}
        ),
        consumption_receipts=consumptions,
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
        dispatch_phase="execute",
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


def test_proposal_withholds_source_capability_and_execute_claim() -> None:
    service, consumptions = _composition()

    context = service.authorize_bound_worker_dispatch(
        job_id="knowledge-index-bound-destination",
        authenticated_worker_id="worker-index-01",
        destination_selection=_selection("model-a").__dict__,
        dispatch_phase="propose",
    )

    assert "source_access_enforcement_manifest" not in context[
        "knowledge_index_job"
    ]
    assert consumptions.calls == []
    assert service._execution_binding_service.claim_calls == []


def test_short_execute_window_rejects_before_capability_side_effects() -> None:
    service, consumptions = _composition()
    repository = service._repository()
    binding_gate = service._execution_binding_service
    binding_gate.envelope["assignment"]["lease_expires_epoch_ms"] = int(
        (NOW + timedelta(seconds=89)).timestamp() * 1000
    )
    repository.task["worker_execution_context"]["knowledge_index_job"] = (
        dict(binding_gate.envelope)
    )

    with pytest.raises(
        ValueError,
        match="knowledge_index_execution_dispatch_window_insufficient",
    ):
        service.authorize_bound_worker_dispatch(
            job_id="knowledge-index-bound-destination",
            authenticated_worker_id="worker-index-01",
            destination_selection=_selection("model-a").__dict__,
            dispatch_phase="execute",
        )

    assert consumptions.calls == []
    assert consumptions.receipt_calls == []
    assert repository.replace_calls == []
    assert binding_gate.claim_calls == []


def test_persisted_dispatch_manifest_is_revalidated_without_second_consumption() -> None:
    service, consumptions = _composition()
    selection = _selection("model-a").__dict__

    first = service.authorize_bound_worker_dispatch(
        job_id="knowledge-index-bound-destination",
        authenticated_worker_id="worker-index-01",
        destination_selection=selection,
        dispatch_phase="execute",
    )
    second = service.authorize_bound_worker_dispatch(
        job_id="knowledge-index-bound-destination",
        authenticated_worker_id="worker-index-01",
        destination_selection=selection,
        dispatch_phase="execute",
    )

    assert second == first
    assert len(consumptions.calls) == 1


def test_tampered_persisted_dispatch_manifest_is_rejected() -> None:
    service, _consumptions = _composition()
    selection = _selection("model-a").__dict__
    service.authorize_bound_worker_dispatch(
        job_id="knowledge-index-bound-destination",
        authenticated_worker_id="worker-index-01",
        destination_selection=selection,
        dispatch_phase="execute",
    )
    persisted_job = service._repository().task["worker_execution_context"][
        "knowledge_index_job"
    ]
    persisted_job["source_access_enforcement_manifest"]["signature"] = (
        "v1.source-access-test." + "0" * 64
    )

    with pytest.raises(
        ValueError,
        match="delegated_manifest_signature_invalid",
    ):
        service.authorize_bound_worker_dispatch(
            job_id="knowledge-index-bound-destination",
            authenticated_worker_id="worker-index-01",
            destination_selection=selection,
            dispatch_phase="execute",
        )


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
            dispatch_phase="execute",
        )

    assert consumptions.calls == []


def test_manifest_save_crash_recovers_only_the_exact_consumption() -> None:
    service, consumptions = _composition()
    repository = service._repository()
    repository.fail_next_save = True
    selection = _selection("model-a").__dict__

    with pytest.raises(
        RuntimeError,
        match="simulated_task_envelope_save_failure",
    ):
        service.authorize_bound_worker_dispatch(
            job_id="knowledge-index-bound-destination",
            authenticated_worker_id="worker-index-01",
            destination_selection=selection,
            dispatch_phase="execute",
        )

    recovered = service.authorize_bound_worker_dispatch(
        job_id="knowledge-index-bound-destination",
        authenticated_worker_id="worker-index-01",
        destination_selection=selection,
        dispatch_phase="execute",
    )

    assert recovered["knowledge_index_job"][
        "source_access_enforcement_manifest"
    ]["binding_digest"] == consumptions.consumption_digest
    assert len(consumptions.calls) == 2
    assert len(consumptions.receipt_calls) == 1


def test_rotated_previous_manifest_key_remains_verifiable() -> None:
    service, consumptions = _composition()
    selection = _selection("model-a").__dict__
    first = service.authorize_bound_worker_dispatch(
        job_id="knowledge-index-bound-destination",
        authenticated_worker_id="worker-index-01",
        destination_selection=selection,
        dispatch_phase="execute",
    )
    previous = service._source_access_enforcement_service
    current_key = SourceAccessSigningKey(
        key_id="source-access-current",
        secret=b"n" * 32,
    )
    previous_key = SourceAccessSigningKey(
        key_id="source-access-test",
        secret=b"s" * 32,
    )
    service._source_access_enforcement_service = (
        SourceAccessEnforcementService(
            grants=previous._grants,
            consumptions=consumptions,
            signer=HubSourceAccessManifestSigner(current_key),
            manifest_verifier=WorkerSourceAccessManifestVerifier(
                {
                    previous_key.key_id: previous_key.secret,
                    current_key.key_id: current_key.secret,
                }
            ),
            consumption_receipts=consumptions,
        )
    )

    replay = service.authorize_bound_worker_dispatch(
        job_id="knowledge-index-bound-destination",
        authenticated_worker_id="worker-index-01",
        destination_selection=selection,
        dispatch_phase="execute",
    )

    assert replay == first
    assert len(consumptions.calls) == 1
