"""Policy-gated, bounded and non-blocking SFU broadcast instrumentation."""

from __future__ import annotations

import json
import math
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Mapping

from agent.services.sfu_broadcast_metrics_port import (
    SfuBroadcastAuditEventPort,
    SfuBroadcastAuditObservation,
    SfuBroadcastAuditRule,
    SfuBroadcastCounterPort,
    SfuBroadcastGaugePort,
    SfuBroadcastHistogramPort,
    SfuBroadcastMetricPoint,
)
from agent.services.sfu_broadcast_observability_policy import (
    SfuBroadcastObservabilityPolicy,
    SfuBroadcastObservabilityPolicyError,
)


class SfuBroadcastMetricsError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class SfuBroadcastMetricBufferLimits:
    max_items: int = 512
    max_bytes: int = 512 * 1024
    max_age_seconds: float = 30.0
    retry_deadline_seconds: float = 0.025
    max_attempts: int = 3

    def __post_init__(self) -> None:
        values = (self.max_items, self.max_bytes, self.max_attempts)
        if any(isinstance(value, bool) or value <= 0 for value in values):
            raise SfuBroadcastMetricsError("sfu_metrics_buffer_limits_invalid")
        if self.max_age_seconds <= 0 or self.retry_deadline_seconds <= 0:
            raise SfuBroadcastMetricsError("sfu_metrics_buffer_limits_invalid")


@dataclass(frozen=True, slots=True)
class SfuBroadcastMetricRecordResult:
    emitted: bool
    buffered: bool
    reason_code: str


@dataclass(frozen=True, slots=True)
class SfuBroadcastMetricDrainResult:
    exported: int
    expired: int
    remaining: int
    reason_code: str


@dataclass(slots=True)
class _PendingObservation:
    kind: str
    value: SfuBroadcastMetricPoint | SfuBroadcastAuditObservation
    encoded_bytes: int
    enqueued_at: float
    expires_at: float
    attempts: int = 0


class SfuBroadcastMetricsService:
    """Validates synchronously and degrades only observability on sink failure."""

    def __init__(
        self,
        *,
        policy: SfuBroadcastObservabilityPolicy,
        counter_port: SfuBroadcastCounterPort,
        histogram_port: SfuBroadcastHistogramPort,
        gauge_port: SfuBroadcastGaugePort,
        audit_port: SfuBroadcastAuditEventPort,
        audit_catalog: Mapping[str, SfuBroadcastAuditRule] | None = None,
        limits: SfuBroadcastMetricBufferLimits | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._policy = policy
        self._counter_port = counter_port
        self._histogram_port = histogram_port
        self._gauge_port = gauge_port
        self._audit_port = audit_port
        self._audit_catalog = dict(policy.audit_events if audit_catalog is None else audit_catalog)
        self._limits = limits or SfuBroadcastMetricBufferLimits()
        self._clock = clock
        self._pending: deque[_PendingObservation] = deque()
        self._pending_bytes = 0
        self._closed = False

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    @property
    def pending_bytes(self) -> int:
        return self._pending_bytes

    def increment(
        self,
        metric_name: str,
        *,
        unit: str,
        value: int | float,
        labels: Mapping[str, str],
        scope_id: str,
        cohort_size: int,
        now_seconds: float,
    ) -> SfuBroadcastMetricRecordResult:
        return self._record_metric(
            "counter", metric_name, unit, value, labels, scope_id, cohort_size, now_seconds
        )

    def observe(
        self,
        metric_name: str,
        *,
        unit: str,
        value: int | float,
        labels: Mapping[str, str],
        scope_id: str,
        cohort_size: int,
        now_seconds: float,
    ) -> SfuBroadcastMetricRecordResult:
        return self._record_metric(
            "histogram", metric_name, unit, value, labels, scope_id, cohort_size, now_seconds
        )

    def gauge(
        self,
        metric_name: str,
        *,
        unit: str,
        value: int | float,
        labels: Mapping[str, str],
        scope_id: str,
        cohort_size: int,
        now_seconds: float,
    ) -> SfuBroadcastMetricRecordResult:
        return self._record_metric(
            "gauge", metric_name, unit, value, labels, scope_id, cohort_size, now_seconds
        )

    def audit(
        self,
        event_name: str,
        *,
        outcome: str,
        reason_code: str,
        labels: Mapping[str, str],
        now_seconds: float,
    ) -> SfuBroadcastMetricRecordResult:
        self._require_open()
        rule = self._audit_catalog.get(event_name)
        if rule is None:
            raise SfuBroadcastMetricsError("sfu_metrics_audit_event_not_registered")
        if outcome not in rule.outcomes or reason_code not in rule.reason_codes:
            raise SfuBroadcastMetricsError("sfu_metrics_audit_value_not_allowed")
        if set(labels) != set(rule.label_values):
            raise SfuBroadcastMetricsError("sfu_metrics_audit_labels_invalid")
        public_labels: dict[str, str] = {}
        for name, value in labels.items():
            if value not in rule.label_values[name]:
                raise SfuBroadcastMetricsError("sfu_metrics_audit_value_not_allowed")
            public_labels[name] = value
        event = SfuBroadcastAuditObservation(
            name=event_name,
            outcome=outcome,
            reason_code=reason_code,
            labels=public_labels,
            observed_at_seconds=self._finite_time(now_seconds),
        )
        return self._submit("audit", event)

    def retry_pending(self, *, deadline_seconds: float | None = None) -> SfuBroadcastMetricDrainResult:
        self._require_open()
        budget = self._limits.retry_deadline_seconds if deadline_seconds is None else deadline_seconds
        if not isinstance(budget, (int, float)) or isinstance(budget, bool) or not 0 < budget <= 1.0:
            raise SfuBroadcastMetricsError("sfu_metrics_retry_deadline_invalid")
        started = self._clock()
        deadline = started + min(float(budget), self._limits.retry_deadline_seconds)
        expired = self._drop_expired(started)
        exported = 0
        while self._pending and self._clock() <= deadline:
            pending = self._pending[0]
            if pending.attempts >= self._limits.max_attempts:
                self._pop_pending()
                expired += 1
                continue
            try:
                self._dispatch(pending.kind, pending.value)
            except Exception:  # noqa: BLE001 - telemetry must not break realtime paths.
                pending.attempts += 1
                break
            self._pop_pending()
            exported += 1
        reason = "sfu_metrics_retry_complete" if not self._pending else "sfu_metrics_retry_bounded"
        return SfuBroadcastMetricDrainResult(exported, expired, len(self._pending), reason)

    def close(self) -> SfuBroadcastMetricDrainResult:
        if self._closed:
            return SfuBroadcastMetricDrainResult(0, 0, 0, "sfu_metrics_already_closed")
        result = self.retry_pending()
        for port in self._unique_ports():
            close = getattr(port, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:  # noqa: BLE001 - shutdown remains bounded.
                    pass
        self._pending.clear()
        self._pending_bytes = 0
        self._closed = True
        return result

    def _record_metric(
        self,
        kind: str,
        metric_name: str,
        unit: str,
        value: int | float,
        labels: Mapping[str, str],
        scope_id: str,
        cohort_size: int,
        now_seconds: float,
    ) -> SfuBroadcastMetricRecordResult:
        self._require_open()
        try:
            decision = self._policy.evaluate(
                metric_name,
                value=value,
                labels=labels,
                scope_id=scope_id,
                cohort_size=cohort_size,
                now_seconds=now_seconds,
            )
        except SfuBroadcastObservabilityPolicyError as exc:
            raise SfuBroadcastMetricsError(exc.reason_code) from exc
        rule = self._policy.metrics[metric_name]
        if rule.metric_type != kind:
            raise SfuBroadcastMetricsError("sfu_metrics_port_kind_mismatch")
        if rule.unit != unit:
            raise SfuBroadcastMetricsError("sfu_metrics_unit_mismatch")
        if float(value) > rule.allowed_buckets[-1]:
            raise SfuBroadcastMetricsError("sfu_metrics_value_bound_exceeded")
        if not decision.emitted:
            return SfuBroadcastMetricRecordResult(False, False, decision.reason_code)
        point = SfuBroadcastMetricPoint(
            name=metric_name,
            kind=kind,
            unit=unit,
            value=value,
            labels=dict(decision.labels),
            observed_at_seconds=self._finite_time(now_seconds),
        )
        return self._submit(kind, point)

    def _submit(
        self,
        kind: str,
        value: SfuBroadcastMetricPoint | SfuBroadcastAuditObservation,
    ) -> SfuBroadcastMetricRecordResult:
        try:
            self._dispatch(kind, value)
        except Exception:  # noqa: BLE001 - exporter failure is deliberately isolated.
            if self._enqueue(kind, value):
                return SfuBroadcastMetricRecordResult(False, True, "sfu_metrics_export_deferred")
            return SfuBroadcastMetricRecordResult(False, False, "sfu_metrics_backpressure_dropped")
        return SfuBroadcastMetricRecordResult(True, False, "sfu_metrics_exported")

    def _dispatch(
        self,
        kind: str,
        value: SfuBroadcastMetricPoint | SfuBroadcastAuditObservation,
    ) -> None:
        if kind == "counter" and isinstance(value, SfuBroadcastMetricPoint):
            self._counter_port.increment_counter(value)
        elif kind == "histogram" and isinstance(value, SfuBroadcastMetricPoint):
            self._histogram_port.observe_histogram(value)
        elif kind == "gauge" and isinstance(value, SfuBroadcastMetricPoint):
            self._gauge_port.set_gauge(value)
        elif kind == "audit" and isinstance(value, SfuBroadcastAuditObservation):
            self._audit_port.emit_audit_event(value)
        else:
            raise SfuBroadcastMetricsError("sfu_metrics_observation_invalid")

    def _enqueue(
        self,
        kind: str,
        value: SfuBroadcastMetricPoint | SfuBroadcastAuditObservation,
    ) -> bool:
        now = self._clock()
        self._drop_expired(now)
        encoded_bytes = len(
            json.dumps(value.public(), sort_keys=True, separators=(",", ":")).encode("utf-8")
        )
        if (
            encoded_bytes > self._limits.max_bytes
            or len(self._pending) >= self._limits.max_items
            or self._pending_bytes + encoded_bytes > self._limits.max_bytes
        ):
            return False
        self._pending.append(
            _PendingObservation(kind, value, encoded_bytes, now, now + self._limits.max_age_seconds)
        )
        self._pending_bytes += encoded_bytes
        return True

    def _drop_expired(self, now: float) -> int:
        expired = 0
        while self._pending and self._pending[0].expires_at <= now:
            self._pop_pending()
            expired += 1
        return expired

    def _pop_pending(self) -> _PendingObservation:
        value = self._pending.popleft()
        self._pending_bytes -= value.encoded_bytes
        return value

    def _unique_ports(self) -> tuple[object, ...]:
        unique: dict[int, object] = {}
        for port in (self._counter_port, self._histogram_port, self._gauge_port, self._audit_port):
            unique[id(port)] = port
        return tuple(unique.values())

    def _require_open(self) -> None:
        if self._closed:
            raise SfuBroadcastMetricsError("sfu_metrics_service_closed")

    @staticmethod
    def _finite_time(value: float) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise SfuBroadcastMetricsError("sfu_metrics_time_invalid")
        return float(value)


__all__ = [
    "SfuBroadcastMetricBufferLimits",
    "SfuBroadcastMetricDrainResult",
    "SfuBroadcastMetricRecordResult",
    "SfuBroadcastMetricsError",
    "SfuBroadcastMetricsService",
]
