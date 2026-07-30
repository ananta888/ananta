"""Worker-local, content-free model-intelligence metric and event ports."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Mapping, Protocol, runtime_checkable

WORKER_JOB_STATES = frozenset({"queued", "running", "succeeded", "failed", "cancelled"})
WORKER_REASON_CODES = frozenset(
    {
        "accepted",
        "artifact_quota_exceeded",
        "cancelled",
        "disk_quota_exceeded",
        "internal_error",
        "parallelism_quota_exceeded",
        "policy_denied",
        "queue_full",
        "ram_quota_exceeded",
        "timeout",
        "tenant_scope_mismatch",
        "vram_quota_exceeded",
        "worker_crashed",
    }
)
WORKER_OPERATIONAL_SCENARIOS = {
    "queue_overload": ("failed", "queue_full"),
    "disk_pressure": ("failed", "disk_quota_exceeded"),
    "timeout": ("failed", "timeout"),
    "cancellation": ("cancelled", "cancelled"),
    "worker_crash": ("failed", "worker_crashed"),
}
_METRICS = {
    "model_intelligence_jobs_total": ("counter", "jobs"),
    "model_intelligence_job_duration_seconds": ("histogram", "seconds"),
    "model_intelligence_queue_depth": ("gauge", "jobs"),
    "model_intelligence_resource_bytes": ("gauge", "bytes"),
    "model_intelligence_artifact_bytes_total": ("counter", "bytes"),
    "model_intelligence_quota_rejections_total": ("counter", "rejections"),
}
_ALLOWED_LABELS = frozenset({"analysis_kind", "reason_code", "resource", "state"})
_LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}$")
_CORRELATION_RE = re.compile(r"^mi1\.[0-9a-f]{24}$")


class WorkerModelIntelligenceMetricsError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class WorkerModelIntelligenceMetric:
    name: str
    value: int | float
    labels: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.name not in _METRICS:
            raise WorkerModelIntelligenceMetricsError("worker_metric_name_invalid")
        if (
            isinstance(self.value, bool)
            or not isinstance(self.value, (int, float))
            or not math.isfinite(float(self.value))
            or self.value < 0
        ):
            raise WorkerModelIntelligenceMetricsError("worker_metric_value_invalid")
        if set(self.labels) - _ALLOWED_LABELS:
            raise WorkerModelIntelligenceMetricsError("worker_metric_label_invalid")
        normalized: dict[str, str] = {}
        for raw_key, raw_value in self.labels.items():
            key, value = str(raw_key), str(raw_value)
            if not _LABEL_RE.fullmatch(value):
                raise WorkerModelIntelligenceMetricsError("worker_metric_label_value_invalid")
            if key == "state" and value not in WORKER_JOB_STATES:
                raise WorkerModelIntelligenceMetricsError("worker_metric_state_invalid")
            if key == "reason_code" and value not in WORKER_REASON_CODES:
                raise WorkerModelIntelligenceMetricsError("worker_metric_reason_code_invalid")
            normalized[key] = value
        object.__setattr__(self, "labels", dict(sorted(normalized.items())))

    @property
    def kind(self) -> str:
        return _METRICS[self.name][0]

    @property
    def unit(self) -> str:
        return _METRICS[self.name][1]


@dataclass(frozen=True, slots=True)
class WorkerModelIntelligenceOperationalEvent:
    state: str
    reason_code: str
    correlation_id: str

    def __post_init__(self) -> None:
        if self.state not in WORKER_JOB_STATES:
            raise WorkerModelIntelligenceMetricsError("worker_event_state_invalid")
        if self.reason_code not in WORKER_REASON_CODES:
            raise WorkerModelIntelligenceMetricsError("worker_event_reason_code_invalid")
        if not _CORRELATION_RE.fullmatch(self.correlation_id):
            raise WorkerModelIntelligenceMetricsError("worker_event_correlation_invalid")

    def public(self) -> dict[str, str]:
        return {
            "schema": "ananta.model-intelligence.worker-operational-event.v1",
            "state": self.state,
            "reason_code": self.reason_code,
            "correlation_id": self.correlation_id,
        }


@runtime_checkable
class WorkerModelIntelligenceMetricPort(Protocol):
    def observe(self, point: WorkerModelIntelligenceMetric) -> None: ...


@runtime_checkable
class WorkerModelIntelligenceOperationalEventPort(Protocol):
    def emit(self, event: WorkerModelIntelligenceOperationalEvent) -> None: ...


class NullWorkerModelIntelligenceMetricPort:
    def observe(self, point: WorkerModelIntelligenceMetric) -> None:
        del point


class NullWorkerModelIntelligenceOperationalEventPort:
    def emit(self, event: WorkerModelIntelligenceOperationalEvent) -> None:
        del event


class WorkerModelIntelligenceMetrics:
    """Small instrumentation facade; it never receives prompts or artifact paths."""

    def __init__(
        self,
        metric_port: WorkerModelIntelligenceMetricPort | None = None,
        event_port: WorkerModelIntelligenceOperationalEventPort | None = None,
    ) -> None:
        self._metric_port = metric_port or NullWorkerModelIntelligenceMetricPort()
        self._event_port = event_port or NullWorkerModelIntelligenceOperationalEventPort()

    def record_job_state(
        self,
        *,
        state: str,
        reason_code: str,
        analysis_kind: str,
        correlation_id: str,
        duration_seconds: float = 0.0,
    ) -> None:
        labels = {
            "analysis_kind": analysis_kind,
            "reason_code": reason_code,
            "state": state,
        }
        self._metric_port.observe(WorkerModelIntelligenceMetric("model_intelligence_jobs_total", 1, labels))
        if duration_seconds:
            self._metric_port.observe(
                WorkerModelIntelligenceMetric(
                    "model_intelligence_job_duration_seconds",
                    duration_seconds,
                    labels,
                )
            )
        self._event_port.emit(WorkerModelIntelligenceOperationalEvent(state, reason_code, correlation_id))

    def record_queue_depth(self, depth: int) -> None:
        self._metric_port.observe(WorkerModelIntelligenceMetric("model_intelligence_queue_depth", depth))

    def record_resource_bytes(self, *, resource: str, value: int) -> None:
        self._metric_port.observe(
            WorkerModelIntelligenceMetric(
                "model_intelligence_resource_bytes",
                value,
                {"resource": resource},
            )
        )

    def record_operational_scenario(
        self,
        scenario: str,
        *,
        analysis_kind: str,
        correlation_id: str,
    ) -> None:
        try:
            state, reason_code = WORKER_OPERATIONAL_SCENARIOS[scenario]
        except KeyError as exc:
            raise WorkerModelIntelligenceMetricsError("worker_operational_scenario_invalid") from exc
        self.record_job_state(
            state=state,
            reason_code=reason_code,
            analysis_kind=analysis_kind,
            correlation_id=correlation_id,
        )


__all__ = [
    "NullWorkerModelIntelligenceMetricPort",
    "NullWorkerModelIntelligenceOperationalEventPort",
    "WORKER_JOB_STATES",
    "WORKER_OPERATIONAL_SCENARIOS",
    "WORKER_REASON_CODES",
    "WorkerModelIntelligenceMetric",
    "WorkerModelIntelligenceMetricPort",
    "WorkerModelIntelligenceMetrics",
    "WorkerModelIntelligenceMetricsError",
    "WorkerModelIntelligenceOperationalEvent",
    "WorkerModelIntelligenceOperationalEventPort",
]
