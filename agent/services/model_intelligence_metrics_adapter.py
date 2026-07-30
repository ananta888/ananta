"""Dependency-free in-process OpenMetrics adapter for Hub observations."""

from __future__ import annotations

import math
import threading
from collections import defaultdict
from typing import Iterable, Mapping

from agent.services.model_intelligence_observability import (
    METRIC_DEFINITIONS,
    ModelIntelligenceMetricPoint,
)

DEFAULT_HISTOGRAM_BUCKETS = (
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
    30.0,
    60.0,
    120.0,
    300.0,
)
SeriesKey = tuple[str, tuple[tuple[str, str], ...]]


def _number(value: float) -> str:
    if value == int(value):
        return str(int(value))
    return format(value, ".12g")


def _labels(labels: Iterable[tuple[str, str]]) -> str:
    values = tuple(labels)
    if not values:
        return ""
    encoded = ",".join(
        f'{key}="{value.replace(chr(92), chr(92) * 2).replace(chr(34), chr(92) + chr(34))}"'
        for key, value in values
    )
    return "{" + encoded + "}"


class InProcessOpenMetricsAdapter:
    """Thread-safe process-local metrics with deterministic OpenMetrics output."""

    def __init__(self, *, histogram_buckets: Iterable[float] = DEFAULT_HISTOGRAM_BUCKETS) -> None:
        buckets = tuple(sorted({float(value) for value in histogram_buckets}))
        if not buckets or any(not math.isfinite(value) or value <= 0 for value in buckets):
            raise ValueError("model_intelligence_histogram_buckets_invalid")
        self._buckets = buckets
        self._values: dict[SeriesKey, float] = defaultdict(float)
        self._histogram_counts: dict[tuple[SeriesKey, float], int] = defaultdict(int)
        self._histogram_sums: dict[SeriesKey, float] = defaultdict(float)
        self._histogram_totals: dict[SeriesKey, int] = defaultdict(int)
        self._lock = threading.Lock()

    def observe(self, point: ModelIntelligenceMetricPoint) -> None:
        key: SeriesKey = (point.name, tuple(sorted(point.labels.items())))
        value = float(point.value)
        with self._lock:
            if point.kind == "counter":
                self._values[key] += value
            elif point.kind == "gauge":
                self._values[key] = value
            else:
                self._histogram_sums[key] += value
                self._histogram_totals[key] += 1
                for boundary in self._buckets:
                    if value <= boundary:
                        self._histogram_counts[(key, boundary)] += 1

    def snapshot(self) -> Mapping[str, object]:
        with self._lock:
            return {
                "values": dict(self._values),
                "histogram_counts": dict(self._histogram_counts),
                "histogram_sums": dict(self._histogram_sums),
                "histogram_totals": dict(self._histogram_totals),
            }

    def render_openmetrics(self) -> str:
        with self._lock:
            values = dict(self._values)
            histogram_counts = dict(self._histogram_counts)
            histogram_sums = dict(self._histogram_sums)
            histogram_totals = dict(self._histogram_totals)
        lines: list[str] = []
        names = sorted({key[0] for key in values} | {key[0] for key in histogram_totals})
        for name in names:
            kind, unit = METRIC_DEFINITIONS[name]
            lines.extend((f"# TYPE {name} {kind}", f"# UNIT {name} {unit}"))
            if kind != "histogram":
                for (series_name, labels), value in sorted(values.items()):
                    if series_name == name:
                        lines.append(f"{name}{_labels(labels)} {_number(value)}")
                continue
            for (series_name, labels), total in sorted(histogram_totals.items()):
                if series_name != name:
                    continue
                key: SeriesKey = (series_name, labels)
                for boundary in self._buckets:
                    bucket_labels = tuple(sorted((*labels, ("le", _number(boundary)))))
                    count = histogram_counts.get((key, boundary), 0)
                    lines.append(f"{name}_bucket{_labels(bucket_labels)} {count}")
                infinite_labels = tuple(sorted((*labels, ("le", "+Inf"))))
                lines.append(f"{name}_bucket{_labels(infinite_labels)} {total}")
                lines.append(f"{name}_sum{_labels(labels)} {_number(histogram_sums[key])}")
                lines.append(f"{name}_count{_labels(labels)} {total}")
        lines.append("# EOF")
        return "\n".join(lines) + "\n"


__all__ = ["DEFAULT_HISTOGRAM_BUCKETS", "InProcessOpenMetricsAdapter"]
