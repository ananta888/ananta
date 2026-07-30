from __future__ import annotations

import pytest

from ananta_contracts.model_intelligence import ArtifactRef
from agent.services.model_intelligence_observability import (
    HmacModelIntelligenceCorrelationService,
    JOB_STATES,
    ModelIntelligenceQuotaLimits,
    ModelIntelligenceQuotaPolicy,
    ModelIntelligenceResourceRequest,
    OPERATIONAL_SCENARIO_REASON_CODES,
    TenantResourceSnapshot,
    operational_reason_code,
)
from worker.model_intelligence.metrics import (
    WORKER_JOB_STATES,
    WORKER_OPERATIONAL_SCENARIOS,
    WorkerModelIntelligenceMetric,
    WorkerModelIntelligenceMetrics,
    WorkerModelIntelligenceOperationalEvent,
)


def _artifact() -> ArtifactRef:
    return ArtifactRef(
        artifact_id="artifact-1",
        job_id="job-1",
        kind="analysis.report",
        sha256="b" * 64,
        size_bytes=256,
        media_type="application/json",
    )


def test_hmac_correlation_is_deterministic_and_contains_no_raw_identifiers() -> None:
    service = HmacModelIntelligenceCorrelationService(b"c" * 32)

    first = service.correlate(
        hub_job_id="job-private",
        worker_task_id="task-private",
        artifact_ref=_artifact(),
    )
    second = service.correlate(
        hub_job_id="job-private",
        worker_task_id="task-private",
        artifact_ref=_artifact(),
    )
    serialized = repr(first.public())

    assert first == second
    assert "job-private" not in serialized
    assert "task-private" not in serialized
    assert "artifact-1" not in serialized
    assert first.artifact_scope is not None


@pytest.mark.parametrize(
    ("resource_request", "reason_code"),
    [
        (ModelIntelligenceResourceRequest("tenant-a", disk_bytes=11), "disk_quota_exceeded"),
        (ModelIntelligenceResourceRequest("tenant-a", ram_bytes=11), "ram_quota_exceeded"),
        (ModelIntelligenceResourceRequest("tenant-a", vram_bytes=11), "vram_quota_exceeded"),
        (ModelIntelligenceResourceRequest("tenant-a", parallel_jobs=2), "parallelism_quota_exceeded"),
        (ModelIntelligenceResourceRequest("tenant-a", artifact_bytes=11), "artifact_quota_exceeded"),
    ],
)
def test_quota_policy_rejects_each_resource_before_execution(
    resource_request: ModelIntelligenceResourceRequest,
    reason_code: str,
) -> None:
    policy = ModelIntelligenceQuotaPolicy(
        ModelIntelligenceQuotaLimits(
            max_disk_bytes=10,
            max_ram_bytes=10,
            max_vram_bytes=10,
            max_parallel_jobs=1,
            max_artifact_bytes=10,
        )
    )

    assert (
        policy.decide(TenantResourceSnapshot("tenant-a"), resource_request).reason_code
        == reason_code
    )


def test_quota_boundaries_are_inclusive_and_tenant_mismatch_fails_closed() -> None:
    policy = ModelIntelligenceQuotaPolicy(
        ModelIntelligenceQuotaLimits(10, 10, 1, 10, max_vram_bytes=10)
    )
    exact = ModelIntelligenceResourceRequest(
        "tenant-a",
        disk_bytes=10,
        ram_bytes=10,
        vram_bytes=10,
        artifact_bytes=10,
    )

    assert policy.decide(TenantResourceSnapshot("tenant-a"), exact).allowed is True
    assert (
        policy.decide(TenantResourceSnapshot("tenant-b"), exact).reason_code
        == "tenant_scope_mismatch"
    )


class _MetricSink:
    def __init__(self) -> None:
        self.points: list[WorkerModelIntelligenceMetric] = []

    def observe(self, point: WorkerModelIntelligenceMetric) -> None:
        self.points.append(point)


class _EventSink:
    def __init__(self) -> None:
        self.events: list[WorkerModelIntelligenceOperationalEvent] = []

    def emit(self, event: WorkerModelIntelligenceOperationalEvent) -> None:
        self.events.append(event)


def test_worker_metrics_cover_every_job_state_without_raw_correlation_labels() -> None:
    metric_sink, event_sink = _MetricSink(), _EventSink()
    metrics = WorkerModelIntelligenceMetrics(metric_sink, event_sink)
    reasons = {
        "queued": "accepted",
        "running": "accepted",
        "succeeded": "accepted",
        "failed": "internal_error",
        "cancelled": "cancelled",
    }

    for state in sorted(WORKER_JOB_STATES):
        metrics.record_job_state(
            state=state,
            reason_code=reasons[state],
            analysis_kind="analysis.static",
            correlation_id="mi1." + "d" * 24,
        )

    assert {point.labels["state"] for point in metric_sink.points} == JOB_STATES
    assert all("correlation_id" not in point.labels for point in metric_sink.points)
    assert {event.state for event in event_sink.events} == WORKER_JOB_STATES


def test_operational_scenarios_have_deterministic_hub_and_worker_reason_codes() -> None:
    assert set(OPERATIONAL_SCENARIO_REASON_CODES) == set(WORKER_OPERATIONAL_SCENARIOS)
    for scenario, (_, worker_reason) in WORKER_OPERATIONAL_SCENARIOS.items():
        assert operational_reason_code(scenario) == worker_reason
