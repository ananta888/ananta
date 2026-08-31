from __future__ import annotations

import pytest

from agent.services.dendritic_memory_capability_service import DendriticMemoryCapabilityService
from agent.services.dendritic_memory_job_service import DendriticMemoryDenied, DendriticMemoryJobService
from agent.services.dendritic_memory_policy import DendriticMemoryPolicy
from agent.services.dendritic_memory_state_store import DendriticMemoryStateStore
from ananta_contracts.dendritic_memory import canonical_digest
from ananta_contracts.dendritic_memory_worker import (
    DendriticCheckpointV1,
    DendriticWorkerResultV1,
)
from tests.dendritic_memory.helpers import spec


def _service(tmp_path):
    policy = DendriticMemoryPolicy(enabled=True, mode="mock")
    capability = DendriticMemoryCapabilityService(policy)
    return DendriticMemoryJobService(
        DendriticMemoryStateStore(tmp_path / "jobs.sqlite3"),
        policy=policy,
        capabilities=capability,
        signing_key=b"j" * 32,
    )


def test_job_is_idempotent_fenced_and_cancellable_without_human(tmp_path) -> None:
    service = _service(tmp_path)
    created = service.create(spec=spec().to_dict(), idempotency_key="request-0001")
    replayed = service.create(spec=spec().to_dict(), idempotency_key="request-0001")
    assert replayed["run_id"] == created["run_id"]
    assert replayed["replayed"] is True
    assignment = service.worker_assignment(tenant_id="tenant-1", run_id=created["run_id"])
    assert assignment["contract_version"] == "ananta.dendritic-memory-worker.v1"
    assert assignment["fencing_token"] == 1
    assert assignment["tenant_scope_digest"]
    with pytest.raises(DendriticMemoryDenied, match="authorization_invalid"):
        service.transition(
            tenant_id="tenant-1",
            run_id=created["run_id"],
            attempt_id=created["attempt_id"],
            worker_authorization="bad",
            target_state="running",
            expected_revision=1,
            reason_code="dendritic_worker_running",
        )
    cancelled = service.cancel(tenant_id="tenant-1", run_id=created["run_id"], expected_revision=1)
    assert cancelled["state"] == "cancelled"
    assert cancelled["human_intervention_required"] is False


def test_cross_tenant_job_lookup_does_not_disclose_job(tmp_path) -> None:
    service = _service(tmp_path)
    created = service.create(spec=spec().to_dict(), idempotency_key="request-0001")
    with pytest.raises(KeyError, match="not_found"):
        service.get(tenant_id="tenant-2", run_id=created["run_id"])


def test_failed_attempt_resumes_only_from_exact_fenced_checkpoint(tmp_path) -> None:
    service = _service(tmp_path)
    created = service.create(spec=spec().to_dict(), idempotency_key="request-0001")
    running = service.transition(
        tenant_id="tenant-1",
        run_id=created["run_id"],
        attempt_id=created["attempt_id"],
        worker_authorization=created["worker_authorization"],
        target_state="running",
        expected_revision=1,
        reason_code="dendritic_worker_running",
    )
    checkpoint = DendriticCheckpointV1(
        tenant_id="tenant-1",
        run_id=created["run_id"],
        attempt_id=created["attempt_id"],
        fencing_token=1,
        spec_digest=spec().digest,
        base_model_snapshot_digest="b" * 64,
        configuration_digest=canonical_digest(spec().configuration.to_dict()),
        step=4,
        payload_digest="7" * 64,
    )
    failed_result = DendriticWorkerResultV1(
        run_id=created["run_id"],
        attempt_id=created["attempt_id"],
        fencing_token=1,
        state="failed",
        reason_code="dendritic_worker_execution_failed",
        event_count=4,
        checkpoint=checkpoint,
    )
    failed = service.transition(
        tenant_id="tenant-1",
        run_id=created["run_id"],
        attempt_id=created["attempt_id"],
        worker_authorization=created["worker_authorization"],
        target_state="failed",
        expected_revision=running["revision"],
        reason_code="dendritic_worker_execution_failed",
        result=failed_result.to_dict(),
    )
    resumed = service.resume(
        tenant_id="tenant-1",
        run_id=created["run_id"],
        expected_revision=failed["revision"],
        checkpoint=checkpoint.to_dict(),
    )
    assert resumed["state"] == "retry_queued"
    assert resumed["fencing_token"] == 2
    assert resumed["attempt_id"] != created["attempt_id"]
    with pytest.raises(DendriticMemoryDenied, match="attempt_stale"):
        service.transition(
            tenant_id="tenant-1",
            run_id=created["run_id"],
            attempt_id=created["attempt_id"],
            worker_authorization=created["worker_authorization"],
            target_state="running",
            expected_revision=resumed["revision"],
            reason_code="dendritic_stale_worker_running",
        )


def test_reconciler_fails_expired_queue_without_human(tmp_path) -> None:
    policy = DendriticMemoryPolicy(enabled=True, mode="mock")
    store = DendriticMemoryStateStore(tmp_path / "jobs.sqlite3")
    service = DendriticMemoryJobService(
        store,
        policy=policy,
        capabilities=DendriticMemoryCapabilityService(policy),
        signing_key=b"j" * 32,
    )
    created = service.create(spec=spec().to_dict(), idempotency_key="request-expired")
    expired = {
        **created,
        "deadline_epoch_ms": 1,
        "updated_at": "2020-01-01T00:00:00Z",
    }
    expired.pop("revision")
    store.append(
        "tenant-1",
        created["run_id"],
        expired,
        expected_revision=created["revision"],
    )

    result = service.reconcile(stale_after_seconds=1)
    reconciled = service.get(tenant_id="tenant-1", run_id=created["run_id"])

    assert result["failed"] == 1
    assert result["human_intervention_required"] is False
    assert reconciled["state"] == "failed"
    assert reconciled["reason_code"] == "dendritic_worker_lease_expired"


def test_worker_claim_is_atomic_and_fully_automatic(tmp_path) -> None:
    service = _service(tmp_path)
    created = service.create(spec=spec().to_dict(), idempotency_key="request-claim")

    claim = service.claim_next()

    assert claim["claimed"] is True
    assert claim["assignment"]["run_id"] == created["run_id"]
    assert claim["expected_revision"] == 2
    assert claim["human_intervention_required"] is False
    assert service.claim_next() == {"claimed": False, "human_intervention_required": False}
