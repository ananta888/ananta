"""Small, content-free output ports for SFU broadcast instrumentation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Protocol, runtime_checkable


MetricValue = int | float


@dataclass(frozen=True, slots=True)
class SfuBroadcastMetricPoint:
    """A policy-approved metric point; labels contain no original identifiers."""

    name: str
    kind: str
    unit: str
    value: MetricValue
    labels: Mapping[str, str]
    observed_at_seconds: float

    def public(self) -> dict[str, object]:
        return {
            "name": self.name,
            "kind": self.kind,
            "unit": self.unit,
            "value": self.value,
            "labels": dict(sorted(self.labels.items())),
            "observed_at_seconds": self.observed_at_seconds,
        }


@dataclass(frozen=True, slots=True)
class SfuBroadcastAuditObservation:
    """A closed-vocabulary operational event without payloads or identities."""

    name: str
    outcome: str
    reason_code: str
    labels: Mapping[str, str]
    observed_at_seconds: float

    def public(self) -> dict[str, object]:
        return {
            "name": self.name,
            "outcome": self.outcome,
            "reason_code": self.reason_code,
            "labels": dict(sorted(self.labels.items())),
            "observed_at_seconds": self.observed_at_seconds,
        }


@dataclass(frozen=True, slots=True)
class SfuBroadcastAuditRule:
    outcomes: frozenset[str]
    reason_codes: frozenset[str]
    label_values: Mapping[str, frozenset[str]]


@runtime_checkable
class SfuBroadcastCounterPort(Protocol):
    def increment_counter(self, point: SfuBroadcastMetricPoint) -> None: ...


@runtime_checkable
class SfuBroadcastHistogramPort(Protocol):
    def observe_histogram(self, point: SfuBroadcastMetricPoint) -> None: ...


@runtime_checkable
class SfuBroadcastGaugePort(Protocol):
    def set_gauge(self, point: SfuBroadcastMetricPoint) -> None: ...


@runtime_checkable
class SfuBroadcastAuditEventPort(Protocol):
    def emit_audit_event(self, event: SfuBroadcastAuditObservation) -> None: ...


__all__ = [
    "MetricValue",
    "SfuBroadcastAuditEventPort",
    "SfuBroadcastAuditObservation",
    "SfuBroadcastAuditRule",
    "SfuBroadcastCounterPort",
    "SfuBroadcastGaugePort",
    "SfuBroadcastHistogramPort",
    "SfuBroadcastMetricPoint",
]
