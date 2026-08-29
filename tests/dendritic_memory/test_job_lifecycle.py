from __future__ import annotations

import pytest

from agent.services.dendritic_memory_capability_service import DendriticMemoryCapabilityService
from agent.services.dendritic_memory_job_service import DendriticMemoryDenied, DendriticMemoryJobService
from agent.services.dendritic_memory_policy import DendriticMemoryPolicy
from agent.services.dendritic_memory_state_store import DendriticMemoryStateStore
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
