from __future__ import annotations

import pytest

from agent.services.dspy_engine_capability_service import DspyEngineCapabilityService
from agent.services.dspy_optimization_job_service import DspyOptimizationDenied, DspyOptimizationJobService
from agent.services.dspy_optimization_policy import DspyOptimizationPolicy
from agent.services.dspy_optimization_state_store import DspyOptimizationStateConflict, DspyOptimizationStateStore
from tests.dspy_optimization.helpers import spec


def _service(tmp_path) -> DspyOptimizationJobService:
    policy = DspyOptimizationPolicy(enabled=True, mode="mock")
    capabilities = DspyEngineCapabilityService(policy)
    capabilities.report_worker(
        {
            "state": "available",
            "installed_version": "mock",
            "compatibility_profile": "dspy-mock-v1",
            "reason_code": "dspy_mock_worker_ready",
            "network_probe_performed": False,
        }
    )
    return DspyOptimizationJobService(
        DspyOptimizationStateStore(tmp_path / "jobs.sqlite3"),
        policy=policy,
        capabilities=capabilities,
        signing_key=b"j" * 32,
    )


def test_hub_create_is_idempotent_and_worker_completion_is_fenced(tmp_path) -> None:
    service = _service(tmp_path)
    first = service.create(spec=spec().to_dict(), idempotency_key="request-1")
    replay = service.create(spec=spec().to_dict(), idempotency_key="request-1")
    assert replay["run_id"] == first["run_id"]
    assert replay["replayed"] is True
    running = service.worker_transition(
        tenant_id="tenant-1",
        run_id=first["run_id"],
        attempt_id=first["attempt_id"],
        authorization=first["authorization"],
        target_state="running",
        expected_revision=1,
        reason_code="dspy_worker_started",
    )
    completed = service.worker_transition(
        tenant_id="tenant-1",
        run_id=first["run_id"],
        attempt_id=first["attempt_id"],
        authorization=first["authorization"],
        target_state="completed",
        expected_revision=running["revision"],
        reason_code="dspy_worker_completed",
        artifact={"digest": "a" * 64},
    )
    assert completed["state"] == "completed"
    with pytest.raises((ValueError, DspyOptimizationStateConflict)):
        service.worker_transition(
            tenant_id="tenant-1",
            run_id=first["run_id"],
            attempt_id=first["attempt_id"],
            authorization=first["authorization"],
            target_state="failed",
            expected_revision=2,
            reason_code="dspy_stale_finish",
        )


def test_invalid_attempt_and_automatic_cancel_never_wait_for_a_human(tmp_path) -> None:
    service = _service(tmp_path)
    created = service.create(spec=spec().to_dict(), idempotency_key="request-1")
    with pytest.raises(DspyOptimizationDenied, match="attempt_stale"):
        service.worker_transition(
            tenant_id="tenant-1",
            run_id=created["run_id"],
            attempt_id="other-attempt",
            authorization=created["authorization"],
            target_state="running",
            expected_revision=1,
            reason_code="dspy_worker_started",
        )
    cancelled = service.cancel(tenant_id="tenant-1", run_id=created["run_id"], expected_revision=1)
    assert cancelled["state"] == "cancelled"
    assert cancelled["human_intervention_required"] is False


def test_dry_run_performs_no_model_call_and_reports_hard_limits(tmp_path) -> None:
    result = _service(tmp_path).dry_run(spec=spec().to_dict())
    assert result["admissible"] is True
    assert result["model_call_performed"] is False
    assert result["hard_limits"]["max_model_calls"] == 10
