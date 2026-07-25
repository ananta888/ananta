"""Explicit mail rollout, passive health and bounded circuit-breaking policy."""

from __future__ import annotations

import os
import json
import tempfile
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any
from typing import Protocol

from agent.services.mail_provider_ports import MailProviderResult


class MailRolloutPhase(str, Enum):
    INFRASTRUCTURE_OFF = "infrastructure_off"
    OPERATOR_OPT_IN = "operator_opt_in"
    JMAP_PREFERRED_NEW_AUTO = "jmap_preferred_new_auto"
    OPTIONAL_EXISTING_CUTOVER = "optional_existing_cutover"


_PHASE_ORDER = {
    MailRolloutPhase.INFRASTRUCTURE_OFF: 0,
    MailRolloutPhase.OPERATOR_OPT_IN: 1,
    MailRolloutPhase.JMAP_PREFERRED_NEW_AUTO: 2,
    MailRolloutPhase.OPTIONAL_EXISTING_CUTOVER: 3,
}


@dataclass(frozen=True)
class MailRuntimePolicySnapshot:
    phase: MailRolloutPhase
    network_enabled: bool
    polling_enabled: bool
    active_diagnostics_enabled: bool
    new_auto_prefers_jmap: bool
    existing_cutover_allowed: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase.value,
            "network_enabled": self.network_enabled,
            "polling_enabled": self.polling_enabled,
            "active_diagnostics_enabled": self.active_diagnostics_enabled,
            "new_auto_prefers_jmap": self.new_auto_prefers_jmap,
            "existing_cutover_allowed": self.existing_cutover_allowed,
        }


class MailRuntimePolicy:
    def __init__(self, phase: MailRolloutPhase) -> None:
        self.phase = phase

    @classmethod
    def from_environment(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> "MailRuntimePolicy":
        source = os.environ if environ is None else environ
        raw = str(
            source.get("ANANTA_MAIL_ROLLOUT_PHASE")
            or MailRolloutPhase.INFRASTRUCTURE_OFF.value
        ).strip()
        try:
            phase = MailRolloutPhase(raw)
        except ValueError as exc:
            raise ValueError("mail_rollout_phase_invalid") from exc
        return cls(phase)

    def snapshot(self) -> MailRuntimePolicySnapshot:
        rank = _PHASE_ORDER[self.phase]
        return MailRuntimePolicySnapshot(
            phase=self.phase,
            network_enabled=rank >= 1,
            polling_enabled=rank >= 1,
            active_diagnostics_enabled=rank >= 1,
            new_auto_prefers_jmap=rank >= 2,
            existing_cutover_allowed=rank >= 3,
        )

    def require_network_operation(self, operation: str) -> None:
        if not self.snapshot().network_enabled:
            raise PermissionError("mail_runtime_network_disabled")
        if operation == "cutover" and not self.snapshot().existing_cutover_allowed:
            raise PermissionError("mail_runtime_cutover_disabled")


@dataclass
class _BreakerRecord:
    state: str = "closed"
    failures: int = 0
    open_until: float = 0.0
    probe_in_flight: bool = False


class MailRuntimeAvailabilityPort(Protocol):
    """Narrow provider-backend seam; the Hub owns the persisted policy."""

    def evaluate(
        self,
        *,
        account_id: str,
        provider: str,
        operation: str,
    ) -> MailProviderResult[None]: ...

    def record_success(
        self,
        *,
        account_id: str,
        provider: str,
        operation: str,
    ) -> None: ...

    def record_failure(
        self,
        *,
        account_id: str,
        provider: str,
        operation: str,
        retryable: bool,
    ) -> None: ...


class MailProviderAvailabilityAdapter:
    """Adapts a provider breaker to the backend's phase-oriented port."""

    def __init__(self, *, provider: str, breaker: "MailCircuitBreaker") -> None:
        normalized = str(provider or "").strip().lower()
        if normalized not in {"jmap", "imap"}:
            raise ValueError("mail_circuit_provider_invalid")
        self._provider = normalized
        self._breaker = breaker

    def allow(self, *, account_id: str, phase: str) -> bool:
        del phase
        return self._breaker.allow(
            account_id=account_id,
            provider=self._provider,
        )

    def record_success(self, *, account_id: str, phase: str) -> None:
        del phase
        self._breaker.record_success(
            account_id=account_id,
            provider=self._provider,
        )

    def record_failure(
        self,
        *,
        account_id: str,
        phase: str,
        retryable: bool,
    ) -> None:
        del phase, retryable
        self._breaker.record_failure(
            account_id=account_id,
            provider=self._provider,
        )


class MailRuntimeAvailabilityPolicy:
    """Combines rollout gating and the persistent provider circuit breaker."""

    def __init__(
        self,
        *,
        runtime_policy: MailRuntimePolicy,
        circuit_breaker: "MailCircuitBreaker",
    ) -> None:
        self._runtime = runtime_policy
        self._circuit = circuit_breaker

    def evaluate(
        self,
        *,
        account_id: str,
        provider: str,
        operation: str,
    ) -> MailProviderResult[None]:
        try:
            self._runtime.require_network_operation(operation)
        except PermissionError as exc:
            return MailProviderResult.failure(str(exc))
        if not self._circuit.allow(
            account_id=account_id,
            provider=provider,
        ):
            return MailProviderResult.failure(
                "mail_provider_circuit_open",
                retryable=True,
            )
        return MailProviderResult.success(
            reason_code="mail_provider_available"
        )

    def record_success(
        self,
        *,
        account_id: str,
        provider: str,
        operation: str,
    ) -> None:
        del operation
        self._circuit.record_success(
            account_id=account_id,
            provider=provider,
        )

    def record_failure(
        self,
        *,
        account_id: str,
        provider: str,
        operation: str,
        retryable: bool,
    ) -> None:
        del operation
        if retryable:
            self._circuit.record_failure(
                account_id=account_id,
                provider=provider,
            )


class MailCircuitBreaker:
    """Per local-account/provider breaker; metrics never receive account labels."""

    def __init__(
        self,
        *,
        failure_threshold: int = 3,
        cooldown_seconds: int = 60,
        clock: Callable[[], float] = time.time,
        on_state_change: Callable[[str, str], None] | None = None,
        state_path: str | Path | None = None,
    ) -> None:
        if failure_threshold < 1 or failure_threshold > 20:
            raise ValueError("mail_circuit_failure_threshold_invalid")
        if cooldown_seconds < 1 or cooldown_seconds > 3600:
            raise ValueError("mail_circuit_cooldown_invalid")
        self._failure_threshold = int(failure_threshold)
        self._cooldown = int(cooldown_seconds)
        self._clock = clock
        self._on_state_change = on_state_change or (lambda _provider, _state: None)
        self._lock = threading.RLock()
        configured_path = (
            state_path
            if state_path is not None
            else os.environ.get(
                "ANANTA_MAIL_CIRCUIT_STATE_PATH",
                "data/mail/circuit-breakers.json",
            )
        )
        self._state_path = Path(configured_path).resolve()
        self._records = self._load_records()

    def _load_records(self) -> dict[tuple[str, str], _BreakerRecord]:
        if not self._state_path.exists():
            return {}
        try:
            payload = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError("mail_circuit_state_invalid") from exc
        if not isinstance(payload, dict) or payload.get("schema") != "ananta.mail-circuit-state.v1":
            raise RuntimeError("mail_circuit_state_invalid")
        records: dict[tuple[str, str], _BreakerRecord] = {}
        for item in payload.get("records") or ():
            if not isinstance(item, Mapping):
                continue
            key = self._key(
                str(item.get("account_id") or ""),
                str(item.get("provider") or ""),
            )
            state = str(item.get("state") or "closed")
            if state not in {"closed", "open", "half_open"}:
                raise RuntimeError("mail_circuit_state_invalid")
            records[key] = _BreakerRecord(
                state=state,
                failures=max(0, int(item.get("failures") or 0)),
                open_until=max(0.0, float(item.get("open_until") or 0.0)),
                probe_in_flight=False,
            )
        return records

    def _persist_locked(self) -> None:
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema": "ananta.mail-circuit-state.v1",
            "records": [
                {
                    "account_id": account_id,
                    "provider": provider,
                    "state": record.state,
                    "failures": record.failures,
                    "open_until": record.open_until,
                }
                for (account_id, provider), record in sorted(self._records.items())
            ],
        }
        descriptor, temp_name = tempfile.mkstemp(
            prefix=f".{self._state_path.name}.",
            suffix=".tmp",
            dir=self._state_path.parent,
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=True, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self._state_path)
        except BaseException:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass
            raise

    @staticmethod
    def _key(account_id: str, provider: str) -> tuple[str, str]:
        account = str(account_id or "").strip()
        protocol = str(provider or "").strip().lower()
        if not account:
            raise ValueError("mail_circuit_account_required")
        if protocol not in {"jmap", "imap"}:
            raise ValueError("mail_circuit_provider_invalid")
        return account, protocol

    def allow(self, *, account_id: str, provider: str) -> bool:
        key = self._key(account_id, provider)
        with self._lock:
            record = self._records.setdefault(key, _BreakerRecord())
            now = float(self._clock())
            if record.state == "open" and now >= record.open_until:
                record.state = "half_open"
                record.probe_in_flight = False
                self._on_state_change(key[1], record.state)
                self._persist_locked()
            if record.state == "open":
                return False
            if record.state == "half_open":
                if record.probe_in_flight:
                    return False
                record.probe_in_flight = True
                self._persist_locked()
            return True

    def record_success(self, *, account_id: str, provider: str) -> None:
        key = self._key(account_id, provider)
        with self._lock:
            record = self._records.setdefault(key, _BreakerRecord())
            changed = record.state != "closed"
            record.state = "closed"
            record.failures = 0
            record.open_until = 0.0
            record.probe_in_flight = False
            if changed:
                self._on_state_change(key[1], record.state)
            self._persist_locked()

    def record_failure(self, *, account_id: str, provider: str) -> None:
        key = self._key(account_id, provider)
        with self._lock:
            record = self._records.setdefault(key, _BreakerRecord())
            record.failures += 1
            if (
                record.state == "half_open"
                or record.failures >= self._failure_threshold
            ):
                changed = record.state != "open"
                record.state = "open"
                record.open_until = float(self._clock()) + self._cooldown
                record.probe_in_flight = False
                if changed:
                    self._on_state_change(key[1], record.state)
            self._persist_locked()

    def state(self, *, account_id: str, provider: str) -> str:
        key = self._key(account_id, provider)
        with self._lock:
            return self._records.setdefault(key, _BreakerRecord()).state

    def close_account(self, account_id: str) -> None:
        account = str(account_id or "").strip()
        with self._lock:
            for key in [item for item in self._records if item[0] == account]:
                self._records.pop(key, None)
            self._persist_locked()


class PassiveMailHealthRegistry:
    COMPONENTS = ("config", "discovery", "auth", "api", "sync", "store")
    STATUSES = frozenset({"ok", "degraded", "disabled", "unknown"})

    def __init__(self, *, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._rows: dict[str, dict[str, Any]] = {
            component: {
                "status": "unknown",
                "reason_code": "mail_health_not_observed",
                "observed_at": None,
            }
            for component in self.COMPONENTS
        }
        self._lock = threading.RLock()

    def observe(self, component: str, *, status: str, reason_code: str) -> None:
        name = str(component or "").strip().lower()
        normalized_status = str(status or "").strip().lower()
        reason = str(reason_code or "").strip().lower()
        if name not in self.COMPONENTS:
            raise ValueError("mail_health_component_invalid")
        if normalized_status not in self.STATUSES:
            raise ValueError("mail_health_status_invalid")
        if not reason.startswith("mail_") or not reason.replace("_", "").isalnum():
            raise ValueError("mail_health_reason_invalid")
        with self._lock:
            self._rows[name] = {
                "status": normalized_status,
                "reason_code": reason,
                "observed_at": float(self._clock()),
            }

    def snapshot(self) -> dict[str, Any]:
        """Pure read: this method never probes a provider or opens a socket."""
        with self._lock:
            rows = {key: dict(value) for key, value in self._rows.items()}
        return {"mode": "passive", "network_calls": 0, "components": rows}


_RUNTIME_POLICY: MailRuntimePolicy | None = None
_CIRCUIT_BREAKER: MailCircuitBreaker | None = None
_AVAILABILITY_POLICY: MailRuntimeAvailabilityPolicy | None = None
_HEALTH_REGISTRY = PassiveMailHealthRegistry()
_RUNTIME_LOCK = threading.Lock()


def get_mail_runtime_policy() -> MailRuntimePolicy:
    global _RUNTIME_POLICY
    if _RUNTIME_POLICY is None:
        with _RUNTIME_LOCK:
            if _RUNTIME_POLICY is None:
                _RUNTIME_POLICY = MailRuntimePolicy.from_environment()
    return _RUNTIME_POLICY


def get_mail_health_registry() -> PassiveMailHealthRegistry:
    return _HEALTH_REGISTRY


def get_mail_circuit_breaker() -> MailCircuitBreaker:
    global _CIRCUIT_BREAKER
    if _CIRCUIT_BREAKER is None:
        with _RUNTIME_LOCK:
            if _CIRCUIT_BREAKER is None:
                from agent.adapters.mail_metrics_adapter import MailMetricsAdapter

                _CIRCUIT_BREAKER = MailCircuitBreaker(
                    on_state_change=MailMetricsAdapter().record_circuit_state
                )
    return _CIRCUIT_BREAKER


def get_mail_runtime_availability_policy() -> MailRuntimeAvailabilityPolicy:
    global _AVAILABILITY_POLICY
    if _AVAILABILITY_POLICY is None:
        runtime_policy = get_mail_runtime_policy()
        circuit_breaker = get_mail_circuit_breaker()
        with _RUNTIME_LOCK:
            if _AVAILABILITY_POLICY is None:
                _AVAILABILITY_POLICY = MailRuntimeAvailabilityPolicy(
                    runtime_policy=runtime_policy,
                    circuit_breaker=circuit_breaker,
                )
    return _AVAILABILITY_POLICY


__all__ = [
    "MailCircuitBreaker",
    "MailRolloutPhase",
    "MailRuntimePolicy",
    "MailRuntimeAvailabilityPort",
    "MailRuntimeAvailabilityPolicy",
    "MailProviderAvailabilityAdapter",
    "MailRuntimePolicySnapshot",
    "PassiveMailHealthRegistry",
    "get_mail_circuit_breaker",
    "get_mail_health_registry",
    "get_mail_runtime_policy",
    "get_mail_runtime_availability_policy",
]
