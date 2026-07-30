"""Dependency-free worker-side OpenMetrics sink."""

from __future__ import annotations

import math
import threading
from collections import defaultdict
from typing import Iterable

from worker.model_intelligence.metrics import WorkerModelIntelligenceMetric

DEFAULT_WORKER_HISTOGRAM_BUCKETS = (
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
    return str(int(value)) if value == int(value) else format(value, ".12g")


def _labels(labels: tuple[tuple[str, str], ...]) -> str:
    if not labels:
        return ""
    return "{" + ",".join(f'{key}="{value}"' for key, value in labels) + "}"


class WorkerInProcessOpenMetricsPort:
    def __init__(
        self,
        *,
        histogram_buckets: Iterable[float] = DEFAULT_WORKER_HISTOGRAM_BUCKETS,
    ) -> None:
        buckets = tuple(sorted({float(value) for value in histogram_buckets}))
        if not buckets or any(not math.isfinite(value) or value <= 0 for value in buckets):
            raise ValueError("worker_histogram_buckets_invalid")
        self._buckets = buckets
        self._values: dict[SeriesKey, float] = defaultdict(float)
        self._histogram_counts: dict[tuple[SeriesKey, float], int] = defaultdict(int)
        self._histogram_sums: dict[SeriesKey, float] = defaultdict(float)
        self._histogram_totals: dict[SeriesKey, int] = defaultdict(int)
        self._definitions: dict[str, tuple[str, str]] = {}
        self._lock = threading.Lock()

    def observe(self, point: WorkerModelIntelligenceMetric) -> None:
        key: SeriesKey = (point.name, tuple(sorted(point.labels.items())))
        with self._lock:
            self._definitions[point.name] = (point.kind, point.unit)
            if point.kind == "counter":
                self._values[key] += float(point.value)
            elif point.kind == "gauge":
                self._values[key] = float(point.value)
            else:
                value = float(point.value)
                self._histogram_sums[key] += value
                self._histogram_totals[key] += 1
                for boundary in self._buckets:
                    if value <= boundary:
                        self._histogram_counts[(key, boundary)] += 1

    def render_openmetrics(self) -> str:
        with self._lock:
            definitions = dict(self._definitions)
            values = dict(self._values)
            counts = dict(self._histogram_counts)
            sums = dict(self._histogram_sums)
            totals = dict(self._histogram_totals)
        lines: list[str] = []
        for name, (kind, unit) in sorted(definitions.items()):
            lines.extend((f"# TYPE {name} {kind}", f"# UNIT {name} {unit}"))
            if kind != "histogram":
                for (series_name, labels), value in sorted(values.items()):
                    if series_name == name:
                        lines.append(f"{name}{_labels(labels)} {_number(value)}")
                continue
            for (series_name, labels), total in sorted(totals.items()):
                if series_name != name:
                    continue
                key: SeriesKey = (series_name, labels)
                for boundary in self._buckets:
                    bucket_labels = tuple(sorted((*labels, ("le", _number(boundary)))))
                    lines.append(
                        f"{name}_bucket{_labels(bucket_labels)} "
                        f"{counts.get((key, boundary), 0)}"
                    )
                infinite_labels = tuple(sorted((*labels, ("le", "+Inf"))))
                lines.append(f"{name}_bucket{_labels(infinite_labels)} {total}")
                lines.append(f"{name}_sum{_labels(labels)} {_number(sums[key])}")
                lines.append(f"{name}_count{_labels(labels)} {total}")
        lines.append("# EOF")
        return "\n".join(lines) + "\n"


__all__ = ["DEFAULT_WORKER_HISTOGRAM_BUCKETS", "WorkerInProcessOpenMetricsPort"]
