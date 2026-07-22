"""Receiver-isolated authoritative TURN degradation state machine."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from threading import RLock
from typing import Callable, Protocol


class WebrtcTurnDegradationError(ValueError):
    def __init__(self, reason_code: str, status_code: int = 400) -> None:
        self.reason_code = reason_code
        self.status_code = status_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class TurnDegradationSignal:
    receiver_ref: str
    expected_version: int
    event: str
    quota_decision: str
    credential_valid: bool
    pool_available: bool
    direct_available: bool
    encryption_required: bool
    encryption_available: bool
    parent_fallback_allowed: bool
    control_allowed: bool
    key_allowed: bool
    transcript_allowed: bool


@dataclass(frozen=True, slots=True)
class TurnReceiverState:
    receiver_diagnostic_ref: str
    version: int
    state: str
    reason_code: str
    allowed_layer: str
    allowed_classes: tuple[str, ...]
    retry_count: int
    cooldown_until_seconds: int
    signature: str

    def public(self) -> dict[str, object]:
        return {
            "receiver_diagnostic_ref": self.receiver_diagnostic_ref,
            "version": self.version,
            "state": self.state,
            "reason_code": self.reason_code,
            "allowed_layer": self.allowed_layer,
            "allowed_classes": list(self.allowed_classes),
            "retry_count": self.retry_count,
            "cooldown_until_seconds": self.cooldown_until_seconds,
            "signature": self.signature,
        }


class TurnReceiverStatePort(Protocol):
    def get(self, receiver_key: str) -> TurnReceiverState | None: ...

    def compare_and_set(self, receiver_key: str, expected_version: int, state: TurnReceiverState) -> bool: ...


class InMemoryTurnReceiverStatePort:
    """Bounded per-receiver state; production requires shared durable CAS storage."""

    def __init__(self, *, max_receivers: int = 10_000) -> None:
        self._max = max_receivers
        self._values: dict[str, TurnReceiverState] = {}
        self._lock = RLock()

    def get(self, receiver_key: str) -> TurnReceiverState | None:
        with self._lock:
            return self._values.get(receiver_key)

    def compare_and_set(self, receiver_key: str, expected_version: int, state: TurnReceiverState) -> bool:
        with self._lock:
            current = self._values.get(receiver_key)
            version = current.version if current else 0
            if version != expected_version:
                return False
            if current is None and len(self._values) >= self._max:
                raise WebrtcTurnDegradationError("turn_degradation_state_capacity_exceeded", 503)
            self._values[receiver_key] = state
            return True


class WebrtcTurnDegradationPolicy:
    STATES = frozenset({"direct", "relay_ok", "relay_capped", "control_only", "fallback", "rejected"})
    EVENTS = frozenset({"admitted", "capacity", "credential_expired", "pool_unavailable", "network_changed", "recovered"})

    def __init__(
        self,
        repository: TurnReceiverStatePort,
        *,
        signing_secret: bytes,
        retry_max: int = 3,
        cooldown_seconds: int = 10,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if len(signing_secret) < 32 or retry_max < 1 or cooldown_seconds < 1:
            raise WebrtcTurnDegradationError("turn_degradation_configuration_invalid", 503)
        self._repository = repository
        self._secret = bytes(signing_secret)
        self._retry_max = retry_max
        self._cooldown = cooldown_seconds
        self._clock = clock

    def transition(self, signal: TurnDegradationSignal) -> TurnReceiverState:
        self._validate(signal)
        key = self._digest("receiver", signal.receiver_ref)
        current = self._repository.get(key)
        current_version = current.version if current else 0
        if signal.expected_version != current_version:
            raise WebrtcTurnDegradationError("turn_degradation_version_conflict", 409)
        now = int(self._clock())
        if current and current.cooldown_until_seconds > now and signal.event != "recovered":
            return current
        state, reason, layer, classes = self._decide(signal)
        retry_count = 0 if state in {"direct", "relay_ok"} else (current.retry_count if current else 0) + 1
        if retry_count > self._retry_max and state not in {"fallback", "rejected"}:
            if signal.parent_fallback_allowed:
                state, reason, layer, classes = "fallback", "turn_retry_budget_exhausted_fallback", "none", ("control",)
            else:
                state, reason, layer, classes = "rejected", "turn_retry_budget_exhausted", "none", ()
        version = current_version + 1
        values = {
            "receiver_diagnostic_ref": "trd1." + key[:24],
            "version": version,
            "state": state,
            "reason_code": reason,
            "allowed_layer": layer,
            "allowed_classes": tuple(classes),
            "retry_count": retry_count,
            "cooldown_until_seconds": now + (self._cooldown if retry_count else 0),
        }
        signature = self._sign(values)
        result = TurnReceiverState(**values, signature=signature)
        if not self._repository.compare_and_set(key, current_version, result):
            raise WebrtcTurnDegradationError("turn_degradation_version_conflict", 409)
        return result

    def verify(self, state: TurnReceiverState) -> bool:
        values = state.public()
        signature = str(values.pop("signature"))
        values["allowed_classes"] = tuple(values["allowed_classes"])
        return hmac.compare_digest(signature, self._sign(values))

    def _decide(self, signal: TurnDegradationSignal) -> tuple[str, str, str, tuple[str, ...]]:
        if signal.encryption_required and not signal.encryption_available:
            return "rejected", "turn_encryption_requirement_unmet", "none", ()
        if signal.direct_available:
            return "direct", "turn_direct_path_available", "high", self._classes(signal, media=True)
        if signal.credential_valid and signal.pool_available:
            if signal.quota_decision == "allow":
                return "relay_ok", "turn_relay_available", "high", self._classes(signal, media=True)
            if signal.quota_decision == "lower_cap":
                return "relay_capped", "turn_relay_lower_cap", "low", self._classes(signal, media=True)
            if signal.quota_decision == "relay_capacity_exhausted" and (signal.control_allowed or signal.key_allowed):
                return "control_only", "turn_relay_control_only", "none", self._classes(signal, media=False)
        if signal.parent_fallback_allowed:
            return "fallback", "turn_parent_fallback_required", "none", self._classes(signal, media=False)
        reason = "turn_credential_expired" if not signal.credential_valid else "turn_relay_unavailable"
        return "rejected", reason, "none", ()

    @staticmethod
    def _classes(signal: TurnDegradationSignal, *, media: bool) -> tuple[str, ...]:
        values: list[str] = []
        if signal.control_allowed:
            values.append("control")
        if signal.key_allowed:
            values.append("key")
        if signal.transcript_allowed:
            values.append("transcript")
        if media:
            values.append("media")
        return tuple(values)

    def _validate(self, signal: TurnDegradationSignal) -> None:
        if not isinstance(signal.receiver_ref, str) or not signal.receiver_ref or len(signal.receiver_ref) > 128:
            raise WebrtcTurnDegradationError("turn_degradation_receiver_invalid")
        if isinstance(signal.expected_version, bool) or not isinstance(signal.expected_version, int) or signal.expected_version < 0:
            raise WebrtcTurnDegradationError("turn_degradation_version_invalid")
        if signal.event not in self.EVENTS or signal.quota_decision not in {"allow", "lower_cap", "relay_capacity_exhausted"}:
            raise WebrtcTurnDegradationError("turn_degradation_signal_invalid")
        bools = (
            signal.credential_valid,
            signal.pool_available,
            signal.direct_available,
            signal.encryption_required,
            signal.encryption_available,
            signal.parent_fallback_allowed,
            signal.control_allowed,
            signal.key_allowed,
            signal.transcript_allowed,
        )
        if any(not isinstance(value, bool) for value in bools):
            raise WebrtcTurnDegradationError("turn_degradation_signal_invalid")

    def _digest(self, domain: str, value: str) -> str:
        return hmac.new(self._secret, f"turn-degradation-{domain}-v1\0{value}".encode(), hashlib.sha256).hexdigest()

    def _sign(self, values: dict[str, object]) -> str:
        payload = json.dumps(values, sort_keys=True, separators=(",", ":")).encode()
        return hmac.new(self._secret, b"turn-degradation-state-v1\0" + payload, hashlib.sha256).hexdigest()


__all__ = [
    "InMemoryTurnReceiverStatePort",
    "TurnDegradationSignal",
    "TurnReceiverState",
    "TurnReceiverStatePort",
    "WebrtcTurnDegradationError",
    "WebrtcTurnDegradationPolicy",
]
