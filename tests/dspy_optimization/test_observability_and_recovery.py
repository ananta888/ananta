from __future__ import annotations

from agent.services.dspy_engine_capability_service import DspyEngineCapabilityService
from agent.services.dspy_observability_service import DspyOperationalTelemetry
from agent.services.dspy_optimization_job_service import DspyOptimizationJobService
from agent.services.dspy_optimization_policy import DspyOptimizationPolicy
from agent.services.dspy_optimization_state_store import DspyOptimizationStateStore
from tests.dspy_optimization.helpers import spec


def _service(tmp_path):
    policy = DspyOptimizationPolicy(enabled=True, mode="mock")
    capabilities = DspyEngineCapabilityService(policy)
    capabilities.report_worker(
        {
            "state": "available",
            "installed_version": "mock",
            "compatibility_profile": "mock-v1",
            "reason_code": "ready",
            "network_probe_performed": False,
        }
    )
    telemetry = DspyOperationalTelemetry(max_events=100)
    service = DspyOptimizationJobService(
        DspyOptimizationStateStore(tmp_path / "runs.sqlite3"),
        policy=policy,
        capabilities=capabilities,
        signing_key=b"x" * 32,
        telemetry=telemetry,
    )
    return service, telemetry


def test_lifecycle_emits_bounded_audit_and_recovers_expired_jobs(tmp_path) -> None:
    service, telemetry = _service(tmp_path)
    run = service.create(spec=spec().to_dict(), idempotency_key="request-1")
    recovered = service.recover(tenant_id="tenant-1", timeout_before="9999-01-01T00:00:00Z")
    assert recovered["count"] == 1
    assert recovered["items"][0]["state"] == "failed"
    assert recovered["items"][0]["reason_code"] == "dspy_worker_lease_expired"
    projection = telemetry.projection()
    assert projection["counters"] == {"jobs:created": 1, "jobs:recovered": 1}
    assert all("run_id" not in event for event in projection["recent_events"])
    assert projection["recent_events"][0]["target_digest"] == run["spec_digest"]


def test_worker_telemetry_rejects_raw_or_secret_fields() -> None:
    telemetry = DspyOperationalTelemetry(max_events=100)
    try:
        telemetry.record_worker({"schema": "x", "prompt": "secret"})
    except ValueError as exc:
        assert str(exc) == "dspy_telemetry_event_invalid"
    else:
        raise AssertionError("raw prompt must be rejected")


def test_worker_metrics_are_numeric_and_labels_remain_bounded() -> None:
    telemetry = DspyOperationalTelemetry(max_events=100)
    telemetry.record_worker(
        {
            "schema": "ananta.dspy-lm-call-audit.v1",
            "run_id": "run-1",
            "attempt_id": "attempt-1",
            "binding_id": "provider-binding:" + "a" * 64,
            "role": "student",
            "request_digest": "b" * 64,
            "input_digest": "c" * 64,
            "output_digest": "d" * 64,
            "input_bytes": 10,
            "output_bytes": 4,
            "usage": {"total_tokens": 3},
            "finish_reason": "stop",
            "cache_hit": False,
            "retry_count": 1,
            "latency_ms": 20,
            "rollout_id": "call-0",
            "cost_micros": 2,
            "observed_provider_cost_micros": None,
        }
    )
    projection = telemetry.projection()
    assert projection["measurements"]["latency_ms"] == {"count": 1, "sum": 20.0, "max": 20.0}
    assert projection["counters"]["cache:miss_or_unknown"] == 1
    assert projection["prometheus_label_policy"] == ["kind", "outcome"]
