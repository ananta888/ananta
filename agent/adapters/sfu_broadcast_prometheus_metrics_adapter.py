"""Bounded pull adapter for policy-approved SFU broadcast metrics."""

from __future__ import annotations

import math
import re
from collections import deque
from threading import Lock

from agent.services.sfu_broadcast_metrics_port import (
    SfuBroadcastAuditObservation,
    SfuBroadcastMetricPoint,
)
from agent.services.sfu_broadcast_observability_policy import SfuBroadcastObservabilityPolicy


class SfuBroadcastPrometheusAdapterError(RuntimeError):
    pass


class SfuBroadcastPrometheusMetricsAdapter:
    """Stores bounded aggregate series; callers expose ``render_openmetrics``."""

    def __init__(
        self,
        *,
        policy: SfuBroadcastObservabilityPolicy,
        max_series: int = 2048,
        max_audit_events: int = 256,
    ) -> None:
        if max_series <= 0 or max_audit_events <= 0:
            raise ValueError("sfu_prometheus_adapter_limits_invalid")
        self._policy = policy
        self._max_series = max_series
        self._counters: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
        self._gauges: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
        self._histograms: dict[tuple[str, tuple[tuple[str, str], ...]], tuple[int, float, list[int]]] = {}
        self._audit_events: deque[SfuBroadcastAuditObservation] = deque(maxlen=max_audit_events)
        self._lock = Lock()
        self._closed = False

    def increment_counter(self, point: SfuBroadcastMetricPoint) -> None:
        key = self._validated_key(point, "counter")
        with self._lock:
            self._require_capacity(key, self._counters)
            self._counters[key] = self._counters.get(key, 0.0) + float(point.value)

    def set_gauge(self, point: SfuBroadcastMetricPoint) -> None:
        key = self._validated_key(point, "gauge")
        with self._lock:
            self._require_capacity(key, self._gauges)
            self._gauges[key] = float(point.value)

    def observe_histogram(self, point: SfuBroadcastMetricPoint) -> None:
        key = self._validated_key(point, "histogram")
        buckets = self._policy.metrics[point.name].allowed_buckets
        with self._lock:
            self._require_capacity(key, self._histograms)
            count, total, counts = self._histograms.get(key, (0, 0.0, [0] * len(buckets)))
            for index, upper_bound in enumerate(buckets):
                if float(point.value) <= upper_bound:
                    counts[index] += 1
            self._histograms[key] = count + 1, total + float(point.value), counts

    def emit_audit_event(self, event: SfuBroadcastAuditObservation) -> None:
        self._require_open()
        with self._lock:
            self._audit_events.append(event)

    def drain_audit_events(self) -> tuple[SfuBroadcastAuditObservation, ...]:
        with self._lock:
            result = tuple(self._audit_events)
            self._audit_events.clear()
            return result

    def render_openmetrics(self) -> str:
        self._require_open()
        lines: list[str] = []
        with self._lock:
            for (name, labels), value in sorted(self._counters.items()):
                lines.append(f"{name}_total{self._labels(labels)} {self._number(value)}")
            for (name, labels), value in sorted(self._gauges.items()):
                lines.append(f"{name}{self._labels(labels)} {self._number(value)}")
            for (name, labels), (count, total, counts) in sorted(self._histograms.items()):
                for bound, bucket_count in zip(self._policy.metrics[name].allowed_buckets, counts):
                    bucket_labels = labels + (("le", self._number(bound)),)
                    lines.append(f"{name}_bucket{self._labels(bucket_labels)} {bucket_count}")
                lines.append(f"{name}_count{self._labels(labels)} {count}")
                lines.append(f"{name}_sum{self._labels(labels)} {self._number(total)}")
        lines.append("# EOF")
        return "\n".join(lines) + "\n"

    def close(self) -> None:
        with self._lock:
            self._counters.clear()
            self._gauges.clear()
            self._histograms.clear()
            self._audit_events.clear()
            self._closed = True

    def _validated_key(
        self,
        point: SfuBroadcastMetricPoint,
        expected_kind: str,
    ) -> tuple[str, tuple[tuple[str, str], ...]]:
        self._require_open()
        rule = self._policy.metrics.get(point.name)
        if rule is None or rule.metric_type != expected_kind or point.kind != expected_kind or rule.unit != point.unit:
            raise SfuBroadcastPrometheusAdapterError("sfu_prometheus_metric_contract_invalid")
        if not math.isfinite(float(point.value)) or point.value < 0:
            raise SfuBroadcastPrometheusAdapterError("sfu_prometheus_metric_contract_invalid")
        expected_labels = {label.name for label in rule.labels} | {"scope_pseudonym"}
        if set(point.labels) != expected_labels or not str(point.labels.get("scope_pseudonym", "")).startswith("sfb1."):
            raise SfuBroadcastPrometheusAdapterError("sfu_prometheus_metric_contract_invalid")
        if not re.fullmatch(r"[a-zA-Z_:][a-zA-Z0-9_:]*", point.name):
            raise SfuBroadcastPrometheusAdapterError("sfu_prometheus_metric_contract_invalid")
        return point.name, tuple(sorted((str(key), str(value)) for key, value in point.labels.items()))

    def _require_capacity(self, key: object, target: dict) -> None:
        self._require_open()
        total = len(self._counters) + len(self._gauges) + len(self._histograms)
        if key not in target and total >= self._max_series:
            raise SfuBroadcastPrometheusAdapterError("sfu_prometheus_series_capacity_exceeded")

    def _require_open(self) -> None:
        if self._closed:
            raise SfuBroadcastPrometheusAdapterError("sfu_prometheus_adapter_closed")

    @staticmethod
    def _labels(labels: tuple[tuple[str, str], ...]) -> str:
        if not labels:
            return ""
        encoded = ",".join(
            f'{key}="{value.replace(chr(92), chr(92) * 2).replace(chr(34), chr(92) + chr(34))}"'
            for key, value in labels
        )
        return "{" + encoded + "}"

    @staticmethod
    def _number(value: int | float) -> str:
        number = float(value)
        return str(int(number)) if number.is_integer() else format(number, ".12g")


__all__ = ["SfuBroadcastPrometheusAdapterError", "SfuBroadcastPrometheusMetricsAdapter"]
