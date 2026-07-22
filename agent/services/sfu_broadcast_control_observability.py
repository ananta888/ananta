"""Content-free, non-blocking instrumentation for SFU control decisions."""

from __future__ import annotations

from dataclasses import dataclass
from functools import wraps
from typing import Any, Callable, Mapping, Protocol, TypeVar, cast, runtime_checkable


CONTROL_AUDIT_EVENT = "ananta_sfu_broadcast_control_decision"
CONTROL_OUTCOMES = frozenset(
    {"accepted", "reduced", "rejected", "failed", "blocked", "busy", "completed", "replayed"}
)
CONTROL_REASONS = frozenset(
    {"success", "policy", "capacity", "stale", "conflict", "dependency", "invalid", "unavailable", "timeout", "unknown"}
)
CONTROL_PATH_LABELS: Mapping[str, Mapping[str, str]] = {
    "admission": {"plane": "hub", "security_scope": "shared"},
    "group_projection": {"plane": "hub", "security_scope": "shared"},
    "layer_projection": {"plane": "hub", "security_scope": "private"},
    "route_reconcile": {"plane": "hub", "security_scope": "shared"},
    "fleet_health": {"plane": "sfu", "security_scope": "shared"},
    "fleet_failover": {"plane": "hub", "security_scope": "shared"},
    "turn_ingestion": {"plane": "turn", "security_scope": "shared"},
    "turn_failover": {"plane": "turn", "security_scope": "shared"},
    "key_delivery": {"plane": "hub", "security_scope": "private"},
    "qos_feedback": {"plane": "hub", "security_scope": "private"},
    "capacity_feedback": {"plane": "hub", "security_scope": "shared"},
    "gate_decision": {"plane": "hub", "security_scope": "shared"},
}


@dataclass(frozen=True, slots=True)
class SfuBroadcastControlObservationResult:
    recorded: bool
    buffered: bool
    reason_code: str


@runtime_checkable
class SfuBroadcastControlObservationPort(Protocol):
    def record(self, *, control_path: str, outcome: str, reason_code: str) -> SfuBroadcastControlObservationResult: ...


class SfuBroadcastAuditRecorderPort(Protocol):
    def audit(
        self,
        event_name: str,
        *,
        outcome: str,
        reason_code: str,
        labels: Mapping[str, str],
        now_seconds: float,
    ) -> object: ...


class NullSfuBroadcastControlObservationPort:
    """Compatibility port that makes disabled instrumentation explicit."""

    def record(self, *, control_path: str, outcome: str, reason_code: str) -> SfuBroadcastControlObservationResult:
        del control_path, outcome, reason_code
        return SfuBroadcastControlObservationResult(False, False, "sfu_control_observation_disabled")


class MetricsSfuBroadcastControlObservationAdapter:
    """Maps a closed control vocabulary to the policy-gated audit port."""

    def __init__(self, recorder: SfuBroadcastAuditRecorderPort, *, clock: Callable[[], float]) -> None:
        self._recorder = recorder
        self._clock = clock

    def record(self, *, control_path: str, outcome: str, reason_code: str) -> SfuBroadcastControlObservationResult:
        labels = CONTROL_PATH_LABELS.get(control_path)
        if labels is None or outcome not in CONTROL_OUTCOMES or reason_code not in CONTROL_REASONS:
            return SfuBroadcastControlObservationResult(
                False, False, "sfu_control_observation_not_registered"
            )
        try:
            result = self._recorder.audit(
                CONTROL_AUDIT_EVENT,
                outcome=outcome,
                reason_code=reason_code,
                labels={"control_path": control_path, **labels},
                now_seconds=float(self._clock()),
            )
        except Exception as exc:  # noqa: BLE001 - telemetry never owns the control path.
            safe_reason = getattr(exc, "reason_code", "sfu_control_observation_sink_failed")
            if not isinstance(safe_reason, str) or not safe_reason.startswith("sfu_"):
                safe_reason = "sfu_control_observation_sink_failed"
            return SfuBroadcastControlObservationResult(False, False, safe_reason)
        return SfuBroadcastControlObservationResult(
            bool(getattr(result, "emitted", False)),
            bool(getattr(result, "buffered", False)),
            str(getattr(result, "reason_code", "sfu_control_observation_sink_failed")),
        )


def control_observer_or_null(
    value: SfuBroadcastControlObservationPort | None,
) -> SfuBroadcastControlObservationPort:
    return value if value is not None else NullSfuBroadcastControlObservationPort()


_F = TypeVar("_F", bound=Callable[..., Any])


def observed_control_path(control_path: str) -> Callable[[_F], _F]:
    """Decorate a synchronous domain method without changing its decision."""

    if control_path not in CONTROL_PATH_LABELS:
        raise ValueError("sfu_control_observation_not_registered")

    def decorate(method: _F) -> _F:
        @wraps(method)
        def wrapped(self: object, *args: object, **kwargs: object) -> object:
            observer = getattr(self, "_control_observer", None)
            try:
                result = method(self, *args, **kwargs)
            except Exception as exc:
                _record_without_impact(observer, control_path, "failed", _classify_reason(exc))
                raise
            outcome = _classify_outcome(result)
            _record_without_impact(observer, control_path, outcome, _classify_reason(result))
            return result

        return cast(_F, wrapped)

    return decorate


def _record_without_impact(observer: object, control_path: str, outcome: str, reason: str) -> None:
    if not isinstance(observer, SfuBroadcastControlObservationPort):
        return
    try:
        observer.record(control_path=control_path, outcome=outcome, reason_code=reason)
    except Exception:  # noqa: BLE001 - custom adapters are isolated too.
        return


def _classify_outcome(value: object) -> str:
    admission_allowed = getattr(value, "admission_allowed", None)
    if admission_allowed is False:
        return "reduced"
    accepted = getattr(value, "accepted", None)
    if accepted is True:
        return "accepted"
    if accepted is False and getattr(value, "replayed", False) is True:
        return "replayed"
    status = getattr(value, "status", None)
    if status is None and isinstance(value, Mapping):
        status = value.get("status")
        if status is None and value.get("ok") is True:
            return "accepted"
    normalized = str(getattr(status, "value", status) or "").casefold()
    if normalized in {"approved", "accepted", "active", "created", "updated"}:
        return "accepted"
    if normalized in {"no_go", "blocked", "denied", "rejected"}:
        return "blocked"
    if normalized == "busy":
        return "busy"
    if normalized in {"failed", "error", "timeout"}:
        return "failed"
    if isinstance(value, str) and "required" in value:
        return "reduced"
    return "completed"


def _classify_reason(value: object) -> str:
    raw = getattr(value, "reason_code", None)
    if raw is None:
        reason_codes = getattr(value, "reason_codes", None)
        if isinstance(reason_codes, (tuple, list)) and reason_codes:
            raw = reason_codes[0]
    if raw is None and isinstance(value, str):
        raw = value
    normalized = str(raw or "success").casefold()
    for marker, category in (
        ("capacity", "capacity"),
        ("stale", "stale"),
        ("expired", "stale"),
        ("epoch", "stale"),
        ("fenc", "stale"),
        ("conflict", "conflict"),
        ("policy", "policy"),
        ("denied", "policy"),
        ("forbidden", "policy"),
        ("dependency", "dependency"),
        ("store", "dependency"),
        ("repository", "dependency"),
        ("invalid", "invalid"),
        ("malformed", "invalid"),
        ("unavailable", "unavailable"),
        ("missing", "unavailable"),
        ("timeout", "timeout"),
    ):
        if marker in normalized:
            return category
    return "success" if normalized in {"success", "accepted", "completed"} else "unknown"


__all__ = [
    "CONTROL_AUDIT_EVENT",
    "CONTROL_OUTCOMES",
    "CONTROL_PATH_LABELS",
    "CONTROL_REASONS",
    "MetricsSfuBroadcastControlObservationAdapter",
    "NullSfuBroadcastControlObservationPort",
    "SfuBroadcastControlObservationPort",
    "SfuBroadcastControlObservationResult",
    "control_observer_or_null",
    "observed_control_path",
]
