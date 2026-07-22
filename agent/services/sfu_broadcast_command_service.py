"""Hub-owned, idempotent SFU broadcast command application service."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
from dataclasses import dataclass
from threading import Lock
from typing import Callable, Mapping, Protocol


class SfuBroadcastCommandError(ValueError):
    def __init__(self, reason_code: str, status_code: int = 400) -> None:
        self.reason_code = reason_code
        self.status_code = status_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class SfuBroadcastCommandPrincipal:
    subject: str
    tenant_ref: str
    role: str
    room_scopes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SfuBroadcastCommand:
    room_ref: str
    action: str
    expected_version: int
    confirmed: bool
    options: Mapping[str, object]
    schema: str = "ananta.webrtc.sfu-broadcast-user-intent.v1"
    reason: str = "user_requested"


@dataclass(frozen=True, slots=True)
class SfuBroadcastCommandAuthorization:
    allowed: bool
    reason_code: str


@dataclass(frozen=True, slots=True)
class SfuBroadcastCommandAuditEvent:
    actor_diagnostic_ref: str
    room_diagnostic_ref: str
    action: str
    outcome: str
    reason_code: str
    operation_id: str


@dataclass(frozen=True, slots=True)
class SfuBroadcastCommandExecution:
    accepted: bool
    effective_version: int
    state: str
    reason_code: str
    audit_committed: bool


@dataclass(frozen=True, slots=True)
class SfuBroadcastCommandResult:
    accepted: bool
    effective_version: int
    state: str
    reason_code: str
    command_ref: str
    replayed: bool = False

    def public(self) -> dict[str, object]:
        return {
            "ok": self.accepted,
            "accepted": self.accepted,
            "effective_version": self.effective_version,
            "state": self.state,
            "reason_code": self.reason_code,
            "command_ref": self.command_ref,
            "replayed": self.replayed,
        }


class SfuBroadcastCommandAuthorizationPort(Protocol):
    def authorize(
        self,
        principal: SfuBroadcastCommandPrincipal,
        command: SfuBroadcastCommand,
    ) -> SfuBroadcastCommandAuthorization: ...


class SfuBroadcastCommandExecutorPort(Protocol):
    def execute(
        self,
        principal: SfuBroadcastCommandPrincipal,
        command: SfuBroadcastCommand,
        audit_event: SfuBroadcastCommandAuditEvent,
    ) -> SfuBroadcastCommandExecution:
        """Atomically persist mutation/audit, idempotently keyed by event.operation_id."""
        ...


class SfuBroadcastCommandLedgerPort(Protocol):
    def claim(
        self,
        scope_digest: str,
        key_digest: str,
        request_digest: str,
        now: float,
    ) -> tuple[str, SfuBroadcastCommandResult | None]: ...

    def complete(
        self,
        scope_digest: str,
        key_digest: str,
        request_digest: str,
        result: SfuBroadcastCommandResult,
    ) -> None: ...

    def abort(
        self,
        scope_digest: str,
        key_digest: str,
        request_digest: str,
    ) -> None: ...


@dataclass(slots=True)
class _LedgerEntry:
    request_digest: str
    expires_at: float
    delivery_started_at: float
    delivery_attempts: int = 1
    result: SfuBroadcastCommandResult | None = None


class InMemorySfuBroadcastCommandLedger:
    """Bounded non-durable ledger for tests; production must inject a durable ledger."""

    def __init__(
        self,
        *,
        max_entries: int = 4096,
        retention_seconds: int = 3600,
        delivery_retry_seconds: int = 5,
    ) -> None:
        if (
            max_entries <= 0
            or retention_seconds <= 0
            or not 1 <= delivery_retry_seconds < retention_seconds
        ):
            raise ValueError("sfu_command_ledger_limits_invalid")
        self._max_entries = max_entries
        self._retention = retention_seconds
        self._delivery_retry = delivery_retry_seconds
        self._entries: dict[tuple[str, str], _LedgerEntry] = {}
        self._lock = Lock()

    def claim(
        self,
        scope_digest: str,
        key_digest: str,
        request_digest: str,
        now: float,
    ) -> tuple[str, SfuBroadcastCommandResult | None]:
        with self._lock:
            expired = [key for key, entry in self._entries.items() if entry.expires_at <= now]
            for key in expired:
                del self._entries[key]
            key = scope_digest, key_digest
            existing = self._entries.get(key)
            if existing is not None:
                if existing.request_digest != request_digest:
                    return "conflict", None
                if existing.result is not None:
                    return "replay", existing.result
                if existing.delivery_started_at + self._delivery_retry > now:
                    return "in_progress", None
                existing.delivery_started_at = now
                existing.delivery_attempts += 1
                return "claimed", None
            if len(self._entries) >= self._max_entries:
                return "capacity", None
            self._entries[key] = _LedgerEntry(
                request_digest,
                now + self._retention,
                now,
            )
            return "claimed", None

    def complete(
        self,
        scope_digest: str,
        key_digest: str,
        request_digest: str,
        result: SfuBroadcastCommandResult,
    ) -> None:
        with self._lock:
            entry = self._entries.get((scope_digest, key_digest))
            if entry is None or entry.request_digest != request_digest:
                raise SfuBroadcastCommandError("sfu_command_idempotency_state_invalid", 503)
            entry.result = result

    def abort(self, scope_digest: str, key_digest: str, request_digest: str) -> None:
        with self._lock:
            entry = self._entries.get((scope_digest, key_digest))
            if entry is not None and entry.request_digest == request_digest and entry.result is None:
                del self._entries[(scope_digest, key_digest)]


class SfuBroadcastCommandService:
    ACTIONS = frozenset(
        {
            "start",
            "stop",
            "set_preferences",
            "data_saver",
            "audio_only",
            "quality_preference",
        }
    )
    STATES = frozenset({"inactive", "starting", "active", "stopping", "denied", "unknown"})
    EXECUTION_REASONS = frozenset(
        {
            "sfu_broadcast_started",
            "sfu_broadcast_stopped",
            "sfu_broadcast_preferences_updated",
            "sfu_broadcast_command_noop",
            "sfu_broadcast_parent_not_ready",
            "sfu_broadcast_kill_switch_active",
            "sfu_broadcast_capacity_exceeded",
            "sfu_broadcast_version_conflict",
            "sfu_broadcast_permission_revoked",
            "sfu_broadcast_feature_disabled",
        }
    )
    QUALITY = frozenset({"auto", "low", "medium", "high"})
    SCHEMA = "ananta.webrtc.sfu-broadcast-user-intent.v1"
    REASONS = frozenset(
        {
            "user_requested",
            "accessibility",
            "network_preference",
            "cost_preference",
            "session_end",
        }
    )

    def __init__(
        self,
        *,
        authorizer: SfuBroadcastCommandAuthorizationPort,
        executor: SfuBroadcastCommandExecutorPort,
        ledger: SfuBroadcastCommandLedgerPort,
        diagnostic_secret: bytes,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if len(diagnostic_secret) < 32:
            raise SfuBroadcastCommandError("sfu_command_diagnostic_secret_invalid", 503)
        self._authorizer = authorizer
        self._executor = executor
        self._ledger = ledger
        self._secret = bytes(diagnostic_secret)
        self._clock = clock

    def execute(
        self,
        principal: SfuBroadcastCommandPrincipal,
        command: SfuBroadcastCommand,
        *,
        idempotency_key: str,
    ) -> SfuBroadcastCommandResult:
        normalized = self._validate(principal, command, idempotency_key)
        authorization = self._authorizer.authorize(principal, normalized)
        if not authorization.allowed:
            raise SfuBroadcastCommandError(self._closed_authorization_reason(authorization.reason_code), 403)
        request_digest = self._request_digest(normalized)
        scope_digest = self._hmac("scope", principal.tenant_ref + "\0" + principal.subject)
        key_digest = self._hmac("idempotency", idempotency_key)
        claim, cached = self._ledger.claim(scope_digest, key_digest, request_digest, self._clock())
        if claim == "conflict":
            raise SfuBroadcastCommandError("sfu_command_idempotency_conflict", 409)
        if claim == "in_progress":
            raise SfuBroadcastCommandError("sfu_command_in_progress", 409)
        if claim == "capacity":
            raise SfuBroadcastCommandError("sfu_command_idempotency_capacity_exceeded", 503)
        if claim == "replay" and cached is not None:
            return SfuBroadcastCommandResult(
                cached.accepted,
                cached.effective_version,
                cached.state,
                cached.reason_code,
                cached.command_ref,
                True,
            )
        audit_event = SfuBroadcastCommandAuditEvent(
            actor_diagnostic_ref=self._hmac("actor", principal.subject)[:24],
            room_diagnostic_ref=self._hmac("room", normalized.room_ref)[:24],
            action=normalized.action,
            outcome="requested",
            reason_code="sfu_broadcast_command_authorized",
            operation_id="sfcop1."
            + self._hmac(
                "operation",
                scope_digest + "\0" + key_digest + "\0" + request_digest,
            )[:32],
        )
        try:
            execution = self._executor.execute(principal, normalized, audit_event)
            self._validate_execution(execution)
        except SfuBroadcastCommandError:
            self._ledger.abort(scope_digest, key_digest, request_digest)
            raise
        except Exception as exc:
            # The executor outcome is ambiguous. Keep the claim so a later retry
            # reuses the same operation_id instead of applying a second mutation.
            raise SfuBroadcastCommandError("sfu_command_executor_unavailable", 503) from exc
        result = SfuBroadcastCommandResult(
            execution.accepted,
            execution.effective_version,
            execution.state,
            execution.reason_code,
            "sfc1." + self._hmac("command", request_digest)[:24],
        )
        self._ledger.complete(scope_digest, key_digest, request_digest, result)
        return result

    def _validate(
        self,
        principal: SfuBroadcastCommandPrincipal,
        command: SfuBroadcastCommand,
        idempotency_key: str,
    ) -> SfuBroadcastCommand:
        if not self._safe_ref(principal.subject) or not self._safe_ref(principal.tenant_ref):
            raise SfuBroadcastCommandError("sfu_command_identity_invalid", 401)
        if principal.role not in {"user", "operator", "admin"}:
            raise SfuBroadcastCommandError("sfu_command_role_forbidden", 403)
        if (
            not self._safe_ref(command.room_ref)
            or not isinstance(command.action, str)
            or command.action not in self.ACTIONS
        ):
            raise SfuBroadcastCommandError("sfu_command_invalid")
        if command.schema != self.SCHEMA or command.reason not in self.REASONS:
            raise SfuBroadcastCommandError("sfu_command_intent_invalid")
        if (
            isinstance(command.expected_version, bool)
            or not isinstance(command.expected_version, int)
            or command.expected_version < 0
        ):
            raise SfuBroadcastCommandError("sfu_command_expected_version_invalid")
        if command.confirmed is not True:
            raise SfuBroadcastCommandError("sfu_command_confirmation_required")
        if not isinstance(idempotency_key, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{15,127}", idempotency_key):
            raise SfuBroadcastCommandError("sfu_command_idempotency_key_invalid")
        if not isinstance(command.options, Mapping):
            raise SfuBroadcastCommandError("sfu_command_options_invalid")
        options = dict(command.options)
        allowed_keys = {"data_saver", "audio_only", "quality_preference"}
        if set(options) - allowed_keys or (command.action == "stop" and options):
            raise SfuBroadcastCommandError("sfu_command_options_invalid")
        required_options = {
            "data_saver": {"data_saver"},
            "audio_only": {"audio_only"},
            "quality_preference": {"quality_preference"},
        }
        if command.action in required_options and set(options) != required_options[command.action]:
            raise SfuBroadcastCommandError("sfu_command_options_invalid")
        for name in ("data_saver", "audio_only"):
            if name in options and not isinstance(options[name], bool):
                raise SfuBroadcastCommandError("sfu_command_options_invalid")
        if "quality_preference" in options and options["quality_preference"] not in self.QUALITY:
            raise SfuBroadcastCommandError("sfu_command_options_invalid")
        return SfuBroadcastCommand(
            room_ref=command.room_ref,
            action=command.action,
            expected_version=command.expected_version,
            confirmed=True,
            options=options,
            schema=command.schema,
            reason=command.reason,
        )

    def _validate_execution(self, execution: SfuBroadcastCommandExecution) -> None:
        if (
            not isinstance(execution.accepted, bool)
            or isinstance(execution.effective_version, bool)
            or execution.effective_version < 0
            or execution.state not in self.STATES
            or execution.reason_code not in self.EXECUTION_REASONS
            or execution.audit_committed is not True
        ):
            raise SfuBroadcastCommandError("sfu_command_executor_result_invalid", 503)

    def _request_digest(self, command: SfuBroadcastCommand) -> str:
        payload = json.dumps(
            {
                "room_ref": command.room_ref,
                "action": command.action,
                "expected_version": command.expected_version,
            "confirmed": command.confirmed,
            "options": dict(sorted(command.options.items())),
            "schema": command.schema,
            "reason": command.reason,
        },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def _hmac(self, domain: str, value: str) -> str:
        return hmac.new(self._secret, f"sfu-command-{domain}-v1\0{value}".encode("utf-8"), hashlib.sha256).hexdigest()

    @staticmethod
    def _closed_authorization_reason(value: str) -> str:
        allowed = {
            "sfu_command_room_forbidden",
            "sfu_command_tenant_forbidden",
            "sfu_command_permission_revoked",
            "sfu_command_role_forbidden",
        }
        return value if value in allowed else "sfu_command_forbidden"

    @staticmethod
    def _safe_ref(value: object) -> bool:
        return isinstance(value, str) and bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", value))


__all__ = [
    "InMemorySfuBroadcastCommandLedger",
    "SfuBroadcastCommand",
    "SfuBroadcastCommandAuditEvent",
    "SfuBroadcastCommandAuthorization",
    "SfuBroadcastCommandAuthorizationPort",
    "SfuBroadcastCommandError",
    "SfuBroadcastCommandExecution",
    "SfuBroadcastCommandExecutorPort",
    "SfuBroadcastCommandLedgerPort",
    "SfuBroadcastCommandPrincipal",
    "SfuBroadcastCommandResult",
    "SfuBroadcastCommandService",
]
