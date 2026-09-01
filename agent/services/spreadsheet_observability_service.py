"""Content-free Spreadsheet Studio telemetry and SLO evaluation."""

from __future__ import annotations

import math
import re
import threading
import time
from collections import Counter, deque
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,254}$")
_LABEL = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,63}$")
_OPERATIONS = frozenset(
    {
        "queue_wait",
        "render_recalc",
        "proposal",
        "validation",
        "training",
        "result_ingress",
        "quota",
        "timeout",
        "crashloop",
        "cleanup",
        "artifact_retention",
    }
)
_OUTCOMES = frozenset({"accepted", "completed", "failed", "not_run", "rejected", "replayed"})
_CORRELATION_FIELDS = (
    "trace_id",
    "task_id",
    "worker_job_id",
    "attempt_id",
    "document_id",
    "candidate_id",
    "dataset_id",
    "training_job_id",
    "adapter_id",
)


class SpreadsheetObservabilityError(ValueError):
    """The proposed telemetry value violates the content-safe contract."""


class SpreadsheetMetricPort(Protocol):
    def increment(self, *, operation: str, outcome: str, reason_code: str) -> None: ...

    def observe_duration(self, *, operation: str, outcome: str, seconds: float) -> None: ...

    def set_queue_depth(self, *, status: str, value: int) -> None: ...

    def set_alert(self, *, reason_code: str, active: bool) -> None: ...


class PrometheusSpreadsheetMetricAdapter:
    """Adapter kept outside policy logic so tests can inject a deterministic port."""

    def increment(self, *, operation: str, outcome: str, reason_code: str) -> None:
        from agent.metrics import SPREADSHEET_OPERATIONS_TOTAL

        SPREADSHEET_OPERATIONS_TOTAL.labels(operation, outcome, reason_code).inc()

    def observe_duration(self, *, operation: str, outcome: str, seconds: float) -> None:
        from agent.metrics import SPREADSHEET_OPERATION_DURATION_SECONDS

        SPREADSHEET_OPERATION_DURATION_SECONDS.labels(operation, outcome).observe(seconds)

    def set_queue_depth(self, *, status: str, value: int) -> None:
        from agent.metrics import SPREADSHEET_QUEUE_DEPTH

        SPREADSHEET_QUEUE_DEPTH.labels(status).set(value)

    def set_alert(self, *, reason_code: str, active: bool) -> None:
        from agent.metrics import SPREADSHEET_ALERT_STATE

        SPREADSHEET_ALERT_STATE.labels(reason_code).set(1 if active else 0)


@dataclass(frozen=True, slots=True)
class SpreadsheetCorrelation:
    trace_id: str | None = None
    task_id: str | None = None
    worker_job_id: str | None = None
    attempt_id: str | None = None
    document_id: str | None = None
    candidate_id: str | None = None
    dataset_id: str | None = None
    training_job_id: str | None = None
    adapter_id: str | None = None

    def __post_init__(self) -> None:
        if not any(getattr(self, field) for field in _CORRELATION_FIELDS):
            raise SpreadsheetObservabilityError("spreadsheet_correlation_empty")
        for field in _CORRELATION_FIELDS:
            value = getattr(self, field)
            if value is not None and (not isinstance(value, str) or not _IDENTIFIER.fullmatch(value)):
                raise SpreadsheetObservabilityError(f"spreadsheet_correlation_{field}_invalid")

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "SpreadsheetCorrelation":
        unknown = set(value) - set(_CORRELATION_FIELDS)
        if unknown:
            raise SpreadsheetObservabilityError("spreadsheet_correlation_fields_invalid")
        return cls(**{field: value.get(field) for field in _CORRELATION_FIELDS})  # type: ignore[arg-type]

    def to_dict(self) -> dict[str, str]:
        return {field: value for field in _CORRELATION_FIELDS if (value := getattr(self, field)) is not None}


class SpreadsheetObservabilityService:
    """Bounded diagnostic read model; correlation IDs never become metric labels."""

    _SLO_SECONDS = {
        "queue_wait": 30.0,
        "render_recalc": 90.0,
        "proposal": 120.0,
        "validation": 30.0,
        "training": 14_400.0,
        "result_ingress": 10.0,
        "cleanup": 300.0,
        "artifact_retention": 300.0,
    }

    def __init__(
        self,
        *,
        metrics: SpreadsheetMetricPort | None = None,
        clock=time.time,
        recent_limit: int = 200,
    ) -> None:
        if not 10 <= int(recent_limit) <= 2_000:
            raise SpreadsheetObservabilityError("spreadsheet_observability_limit_invalid")
        self._metrics = metrics or PrometheusSpreadsheetMetricAdapter()
        self._clock = clock
        self._lock = threading.RLock()
        self._events: deque[dict[str, object]] = deque(maxlen=int(recent_limit))
        self._counts: Counter[tuple[str, str, str]] = Counter()
        self._durations: dict[str, deque[float]] = {
            operation: deque(maxlen=int(recent_limit)) for operation in _OPERATIONS
        }
        self._queue_depth = {status: 0 for status in ("dispatch_pending", "queued", "leased", "failed")}
        self._active_alerts: set[str] = set()

    def record(
        self,
        *,
        operation: str,
        outcome: str,
        reason_code: str,
        correlation: SpreadsheetCorrelation,
        duration_seconds: float | None = None,
    ) -> None:
        operation = self._bounded(operation, _OPERATIONS, "operation")
        outcome = self._bounded(outcome, _OUTCOMES, "outcome")
        reason_code = self._bounded(reason_code, None, "reason_code")
        if duration_seconds is not None and (
            isinstance(duration_seconds, bool)
            or not math.isfinite(float(duration_seconds))
            or not 0 <= float(duration_seconds) <= 86_400
        ):
            raise SpreadsheetObservabilityError("spreadsheet_observability_duration_invalid")
        event: dict[str, object] = {
            "operation": operation,
            "outcome": outcome,
            "reason_code": reason_code,
            "correlation": correlation.to_dict(),
            "observed_at": float(self._clock()),
        }
        if duration_seconds is not None:
            event["duration_seconds"] = round(float(duration_seconds), 6)
        with self._lock:
            self._counts[(operation, outcome, reason_code)] += 1
            self._events.append(event)
            if duration_seconds is not None:
                self._durations[operation].append(float(duration_seconds))
        self._metrics.increment(operation=operation, outcome=outcome, reason_code=reason_code)
        if duration_seconds is not None:
            self._metrics.observe_duration(operation=operation, outcome=outcome, seconds=float(duration_seconds))

    def publish_queue_depth(self, counts: Mapping[str, int]) -> None:
        allowed = set(self._queue_depth)
        invalid_values = any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in counts.values()
        )
        if set(counts) - allowed or invalid_values:
            raise SpreadsheetObservabilityError("spreadsheet_queue_depth_invalid")
        with self._lock:
            for status in allowed:
                self._queue_depth[status] = int(counts.get(status, 0))
                self._metrics.set_queue_depth(status=status, value=self._queue_depth[status])

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            alerts: set[str] = set()
            slo: dict[str, object] = {}
            for operation, objective in self._SLO_SECONDS.items():
                samples = sorted(self._durations[operation])
                p95 = self._percentile(samples, 0.95)
                breached = p95 is not None and p95 > objective
                if breached:
                    alerts.add(f"spreadsheet_{operation}_slo_breached")
                slo[operation] = {
                    "objective_seconds": objective,
                    "sample_count": len(samples),
                    "p95_seconds": round(p95, 6) if p95 is not None else None,
                    "status": "breached" if breached else ("met" if samples else "not_run"),
                }
            if self._queue_depth["dispatch_pending"] + self._queue_depth["queued"] >= 25:
                alerts.add("spreadsheet_queue_backpressure")
            if self._queue_depth["failed"] >= 5:
                alerts.add("spreadsheet_execution_failures_high")
            for reason in alerts | self._active_alerts:
                self._metrics.set_alert(reason_code=reason, active=reason in alerts)
            self._active_alerts = alerts
            degraded = bool(alerts)
            return {
                "schema": "ananta.spreadsheet-operations-snapshot.v1",
                "observed_at": float(self._clock()),
                "status": "degraded" if degraded else "healthy",
                "degraded_mode": "base_model_only_and_bounded_queue" if degraded else "none",
                "backpressure_active": "spreadsheet_queue_backpressure" in alerts,
                "safe_shutdown": {
                    "admissions": "stop_before_process_exit",
                    "queue": "durable_hub_owned",
                    "result_ingress": "drain_until_deadline",
                    "worker_outbox": "retry_idempotently",
                },
                "queue_depth": dict(self._queue_depth),
                "slos": slo,
                "alerts": sorted(alerts),
                "counts": [
                    {"operation": key[0], "outcome": key[1], "reason_code": key[2], "count": count}
                    for key, count in sorted(self._counts.items())
                ],
                "recent_correlations": list(reversed(self._events)),
                "human_intervention_required": False,
            }

    @staticmethod
    def _bounded(value: object, allowed: frozenset[str] | None, field: str) -> str:
        normalized = str(value or "").strip().lower()
        if (allowed is not None and normalized not in allowed) or not _LABEL.fullmatch(normalized):
            raise SpreadsheetObservabilityError(f"spreadsheet_observability_{field}_invalid")
        return normalized

    @staticmethod
    def _percentile(samples: list[float], percentile: float) -> float | None:
        if not samples:
            return None
        return samples[min(len(samples) - 1, math.ceil(len(samples) * percentile) - 1)]


__all__ = [
    "PrometheusSpreadsheetMetricAdapter",
    "SpreadsheetCorrelation",
    "SpreadsheetMetricPort",
    "SpreadsheetObservabilityError",
    "SpreadsheetObservabilityService",
]
