"""Content-free observability primitives for source-control operations."""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Mapping, Optional, Protocol, Sequence

_DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,254}$")


class SourceControlObservabilityError(ValueError):
    """Raised when observability data could disclose content or explode labels."""


class SourceControlAuditOperation(str, Enum):
    create = "create"
    validate = "validate"
    scan = "scan"
    refresh = "refresh"
    index = "index"
    activate = "activate"
    rollback = "rollback"
    grant = "grant"
    deny = "deny"
    approval = "approval"
    disable = "disable"
    purge = "purge"
    lifecycle = "lifecycle"
    shadow = "shadow"
    download = "download"


class SourceControlDecision(str, Enum):
    allow = "allow"
    deny = "deny"
    unavailable = "unavailable"
    approval_required = "approval_required"


@dataclass(frozen=True)
class SourceControlAuditEvent:
    operation: SourceControlAuditOperation
    actor_id: str
    tenant_id: str
    project_id: str
    resource_kind: str
    resource_id: str
    trace_id: str
    decision: SourceControlDecision
    reason_code: str
    revision_digest: Optional[str] = None
    manifest_digest: Optional[str] = None
    policy_digest: Optional[str] = None

    def __post_init__(self) -> None:
        for name in (
            "actor_id",
            "tenant_id",
            "project_id",
            "resource_kind",
            "resource_id",
            "trace_id",
            "reason_code",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not _IDENTIFIER_PATTERN.fullmatch(value):
                raise SourceControlObservabilityError(
                    f"{name} must be a bounded opaque identifier"
                )
        for name in ("revision_digest", "manifest_digest", "policy_digest"):
            value = getattr(self, name)
            if value is not None and not _DIGEST_PATTERN.fullmatch(value):
                raise SourceControlObservabilityError(
                    f"{name} must be a lowercase SHA-256 digest"
                )

    def to_payload(self) -> dict[str, str]:
        payload = {
            "actor_id": self.actor_id,
            "tenant_id": self.tenant_id,
            "project_id": self.project_id,
            "resource_kind": self.resource_kind,
            "resource_id": self.resource_id,
            "operation": self.operation.value,
            "decision": self.decision.value,
            "reason_code": self.reason_code,
            "trace_id": self.trace_id,
        }
        for name in ("revision_digest", "manifest_digest", "policy_digest"):
            value = getattr(self, name)
            if value is not None:
                payload[name] = value
        return payload


AuditSink = Callable[..., Any]


def emit_source_control_audit(
    event: SourceControlAuditEvent,
    *,
    sink: Optional[AuditSink] = None,
) -> None:
    """Emit a validated event through the cross-cutting audit facade."""

    if sink is None:
        from agent.common.audit import log_audit

        sink = log_audit
    sink(
        action=f"source_control.{event.operation.value}",
        details=event.to_payload(),
    )


class SourceControlMetricsPort(Protocol):
    """Small metrics port with a deliberately bounded label vocabulary."""

    def observe_duration(
        self,
        metric: str,
        seconds: float,
        labels: Mapping[str, str],
    ) -> None: ...

    def increment(
        self,
        metric: str,
        labels: Mapping[str, str],
    ) -> None: ...

    def set_gauge(
        self,
        metric: str,
        value: float,
        labels: Mapping[str, str],
    ) -> None: ...


_ALLOWED_METRIC_LABELS = frozenset(
    {
        "connector_type",
        "decision",
        "operation",
        "reason_code",
        "status",
    }
)


def bounded_metric_labels(**labels: str) -> dict[str, str]:
    unknown = set(labels) - _ALLOWED_METRIC_LABELS
    if unknown:
        raise SourceControlObservabilityError(
            f"unbounded metric labels are forbidden: {sorted(unknown)}"
        )
    normalized: dict[str, str] = {}
    for key, value in labels.items():
        text = str(value)
        if not text or len(text) > 64 or not re.fullmatch(r"[a-z0-9_.:-]+", text):
            raise SourceControlObservabilityError(
                f"metric label {key} is not bounded"
            )
        normalized[key] = text
    return normalized


@dataclass(frozen=True)
class SourceControlHealth:
    status: str
    stale_sources: int
    authorization_failures: int
    blocked_jobs: int
    hash_drift_events: int
    storage_pressure: bool

    @classmethod
    def from_counters(
        cls,
        *,
        stale_sources: int,
        authorization_failures: int,
        blocked_jobs: int,
        hash_drift_events: int,
        storage_pressure: bool,
    ) -> "SourceControlHealth":
        counters = (
            stale_sources,
            authorization_failures,
            blocked_jobs,
            hash_drift_events,
        )
        if any(not isinstance(value, int) or value < 0 for value in counters):
            raise SourceControlObservabilityError(
                "health counters must be non-negative integers"
            )
        degraded = storage_pressure or any(counters)
        return cls(
            status="degraded" if degraded else "healthy",
            stale_sources=stale_sources,
            authorization_failures=authorization_failures,
            blocked_jobs=blocked_jobs,
            hash_drift_events=hash_drift_events,
            storage_pressure=bool(storage_pressure),
        )


@dataclass(frozen=True)
class SourceControlHealthReport:
    health: SourceControlHealth
    alarms: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": "ananta.source-control.health.v1",
            "status": self.health.status,
            "counters": {
                "stale_sources": self.health.stale_sources,
                "authorization_failures": self.health.authorization_failures,
                "blocked_jobs": self.health.blocked_jobs,
                "hash_drift_events": self.health.hash_drift_events,
            },
            "storage_pressure": self.health.storage_pressure,
            "alarms": list(self.alarms),
        }


class SourceControlHealthMetricsPublisher:
    """Project health snapshots into transition-safe, bounded metrics."""

    def __init__(self, metrics: SourceControlMetricsPort) -> None:
        self._metrics = metrics
        self._lock = threading.RLock()
        self._active_alarms: set[str] = set()

    def publish(self, report: SourceControlHealthReport) -> None:
        active_alarms = set(report.alarms)
        with self._lock:
            for status in ("healthy", "degraded"):
                self._metrics.set_gauge(
                    "source_control_health",
                    1.0 if report.health.status == status else 0.0,
                    bounded_metric_labels(status=status),
                )
            for reason_code in sorted(
                active_alarms | self._active_alarms
            ):
                active = reason_code in active_alarms
                self._metrics.set_gauge(
                    "source_control_alert_state",
                    1.0 if active else 0.0,
                    bounded_metric_labels(
                        reason_code=reason_code,
                        status="firing",
                    ),
                )
                self._metrics.set_gauge(
                    "source_control_alert_state",
                    0.0 if active else 1.0,
                    bounded_metric_labels(
                        reason_code=reason_code,
                        status="resolved",
                    ),
                )
            self._active_alarms = active_alarms


class SourceControlHealthMonitor:
    """Thread-safe, content-free operational state for the Hub composition."""

    _AUTH_MARKERS = (
        "auth",
        "credential",
        "forbidden",
        "policy_denied",
        "scope",
    )
    _BLOCKED_MARKERS = ("blocked", "lease_expired", "stalled")
    _HASH_DRIFT_MARKERS = ("hash_drift", "digest_mismatch")

    def __init__(
        self,
        *,
        stale_alarm_threshold: int = 10,
        authorization_alarm_threshold: int = 5,
    ) -> None:
        if stale_alarm_threshold < 1 or authorization_alarm_threshold < 1:
            raise SourceControlObservabilityError(
                "health alarm thresholds must be positive"
            )
        self._stale_alarm_threshold = stale_alarm_threshold
        self._authorization_alarm_threshold = authorization_alarm_threshold
        self._lock = threading.RLock()
        self._stale_sources = 0
        self._authorization_failures = 0
        self._blocked_jobs = 0
        self._hash_drift_events = 0
        self._storage_pressure = False
        self._operational_alarms: set[str] = set()

    def record_result(
        self,
        *,
        operation: str,
        result: object,
    ) -> None:
        with self._lock:
            if operation == "list_connections":
                mapping = _result_mapping(result)
                items = mapping.get("items", ())
                if isinstance(items, Sequence) and not isinstance(
                    items, (str, bytes)
                ):
                    self._stale_sources = sum(
                        1
                        for item in items
                        if isinstance(item, Mapping)
                        and item.get("stale") is True
                    )
            if operation == "poll_events":
                mapping = _result_mapping(result)
                items = mapping.get("items", mapping.get("events", ()))
                if isinstance(items, Sequence) and not isinstance(
                    items, (str, bytes)
                ):
                    self._blocked_jobs = sum(
                        1
                        for item in items
                        if isinstance(item, Mapping)
                        and str(item.get("status") or "").lower()
                        in {"blocked", "expired", "stalled"}
                    )

    def record_failure(self, reason_code: str) -> None:
        normalized = str(reason_code or "internal_error").lower()
        with self._lock:
            if any(marker in normalized for marker in self._AUTH_MARKERS):
                self._authorization_failures += 1
            if any(marker in normalized for marker in self._BLOCKED_MARKERS):
                self._blocked_jobs += 1
            if any(marker in normalized for marker in self._HASH_DRIFT_MARKERS):
                self._hash_drift_events += 1
            if "storage_pressure" in normalized or "disk_full" in normalized:
                self._storage_pressure = True

    def set_operational_alarm(self, reason_code: str, active: bool = True) -> None:
        if not re.fullmatch(r"[a-z0-9_.:-]{1,64}", str(reason_code or "")):
            raise SourceControlObservabilityError(
                "operational alarm reason must be bounded"
            )
        with self._lock:
            if active:
                self._operational_alarms.add(reason_code)
            else:
                self._operational_alarms.discard(reason_code)

    def snapshot(self) -> SourceControlHealthReport:
        with self._lock:
            health = SourceControlHealth.from_counters(
                stale_sources=self._stale_sources,
                authorization_failures=self._authorization_failures,
                blocked_jobs=self._blocked_jobs,
                hash_drift_events=self._hash_drift_events,
                storage_pressure=self._storage_pressure,
            )
            alarms = set(self._operational_alarms)
            if self._stale_sources >= self._stale_alarm_threshold:
                alarms.add("stale_source_threshold")
            if (
                self._authorization_failures
                >= self._authorization_alarm_threshold
            ):
                alarms.add("authorization_failure_threshold")
            if self._blocked_jobs:
                alarms.add("blocked_jobs")
            if self._hash_drift_events:
                alarms.add("artifact_hash_drift")
            if self._storage_pressure:
                alarms.add("storage_pressure")
            if alarms and health.status == "healthy":
                health = SourceControlHealth(
                    status="degraded",
                    stale_sources=health.stale_sources,
                    authorization_failures=health.authorization_failures,
                    blocked_jobs=health.blocked_jobs,
                    hash_drift_events=health.hash_drift_events,
                    storage_pressure=health.storage_pressure,
                )
            return SourceControlHealthReport(
                health=health,
                alarms=tuple(sorted(alarms)),
            )


def _result_mapping(result: object) -> Mapping[str, Any]:
    if isinstance(result, tuple) and result:
        result = result[0]
    return result if isinstance(result, Mapping) else {}
