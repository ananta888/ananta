"""Measurement records, metric states and small statistics of the semantic-media program benchmark."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping, Sequence


METRIC_FIELDS = (
    "ingress_bytes",
    "egress_bytes",
    "turn_bytes",
    "cpu_micros",
    "gpu_micros",
    "ram_bytes",
    "vram_bytes",
    "disk_bytes",
    "energy_microwh",
    "latency_p50_ms",
    "latency_p95_ms",
    "latency_p99_ms",
    "worst_burst_bytes",
    "recovery_ms",
    "open_resources",
)


class ProgramBenchmarkExecutionError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class MetricState:
    status: str
    method: str
    reason_code: str

    def as_dict(self) -> dict[str, str]:
        return {
            "status": self.status,
            "method": self.method,
            "reason_code": self.reason_code,
        }


@dataclass(frozen=True, slots=True)
class ModeMeasurement:
    values: Mapping[str, int | None]
    availability: Mapping[str, MetricState]
    latency_samples: tuple[float, ...]
    expected_deliveries: int
    completed_deliveries: int
    valid_deliveries: int
    saturation: Mapping[str, int | bool] | None = None

    def as_dict(self, *, binding_sha256: str) -> dict[str, Any]:
        return {
            "binding_sha256": binding_sha256,
            "values": dict(self.values),
            "availability": {
                name: self.availability[name].as_dict() for name in METRIC_FIELDS
            },
            "latency_sample_count": len(self.latency_samples),
            "expected_deliveries": self.expected_deliveries,
            "completed_deliveries": self.completed_deliveries,
            "valid_deliveries": self.valid_deliveries,
            "offline_saturation": dict(self.saturation) if self.saturation is not None else None,
        }


def _delivery_score(value: ModeMeasurement) -> int:
    if value.expected_deliveries <= 0:
        return 0
    return value.valid_deliveries * 1_000_000 // value.expected_deliveries


def _percentile_ms(values: Sequence[float], fraction: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    return max(1, math.ceil(ordered[min(len(ordered) - 1, math.ceil(len(ordered) * fraction) - 1)]))


def _worst_burst(events: Sequence[tuple[int, int]]) -> int:
    buckets: dict[int, int] = {}
    for sent_ns, size in events:
        bucket = sent_ns // 10_000_000
        buckets[bucket] = buckets.get(bucket, 0) + size
    return max(buckets.values(), default=0)


def _measured(method: str) -> MetricState:
    return MetricState("measured", method, "")


def _unavailable(reason_code: str) -> MetricState:
    return MetricState("unavailable", "unavailable", reason_code)


def _not_applicable(reason_code: str) -> MetricState:
    return MetricState("not_applicable", "not_applicable", reason_code)
