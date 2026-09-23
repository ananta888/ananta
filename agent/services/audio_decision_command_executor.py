"""Hub-side execution of AudioDecision voice commands, with explicit one-time confirmations.

``dispatch_audio_decision`` turns a gated ``AudioDecisionCommandResult`` into what the hub does:

* ``act`` (policy rule P12 only): the ``VoiceCommandExecutor`` runs the registered handler for the
  action type, but only after the caller-supplied ``authorize`` check (the voice exposure policy,
  operation ``command``) passes again right before execution.
* ``confirm``: a ``VoiceCommandConfirmationStore`` issues a short-lived, single-use confirmation bound
  to tenant, subject and the concrete action. Nothing runs until an explicit confirmation request
  consumes it (``confirm_voice_command``); expired, replayed, foreign or mismatching confirmations
  are denied. A pending confirmation is never an allow.
* ``deny``/``ask_again``/``system2``/``normal_path``: typed answers only, nothing runs.

Fail-closed everywhere: an unknown action type, a failed authorization, a raising handler or a
result that is not a policy-allowed ``act`` never executes. ``grants_permission`` stays ``False``;
a decision value never grants a permission. Audit projections carry no audio, transcript, label or
confirmation id.
"""
from __future__ import annotations

import os
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from agent.services.audio_decision_command_service import AudioDecisionCommandResult
from agent.services.audio_decision_hub_gate import HubAction, PolicyVerdict
from agent.services.voice_governance_domain import VoicePrincipal

CONFIRM_ROUTE = "/v1/voice/command/confirm"
DEFAULT_CONFIRMATION_TTL_SECONDS = 30.0
_MIN_TTL_SECONDS = 5.0
_MAX_TTL_SECONDS = 300.0
_MAX_PENDING_CONFIRMATIONS = 256

Handler = Callable[["VoiceCommandInvocation"], Mapping[str, Any]]
Authorize = Callable[[], bool]


@dataclass(frozen=True)
class VoiceCommandInvocation:
    """What a handler sees: the typed action, never audio or a transcript."""

    action_type: str
    command: Any
    profile: str
    field: str
    principal: VoicePrincipal


@dataclass(frozen=True)
class VoiceCommandExecution:
    executed: bool
    action_type: str
    error_code: str | None = None
    effect: Mapping[str, Any] = field(default_factory=dict)
    grants_permission: bool = False

    def as_response(self) -> dict[str, Any]:
        return {
            "status": "executed" if self.executed else "denied",
            "action_type": self.action_type,
            "error_code": self.error_code,
            "effect": dict(self.effect) if self.executed else None,
            "grants_permission": False,
        }

    def as_audit_dict(self) -> dict[str, Any]:
        """No effect payload: it may repeat the label."""
        return {
            "status": "executed" if self.executed else "denied",
            "action_type": self.action_type,
            "error_code": self.error_code,
            "grants_permission": False,
        }


def _directive(kind: str, key: str, value: str) -> Handler:
    def handler(_invocation: VoiceCommandInvocation) -> Mapping[str, Any]:
        return {"kind": kind, key: value}

    return handler


def default_handlers() -> dict[str, Handler]:
    """Hub handlers for the policy's ``voice.*`` action types; each yields a typed client directive."""
    handlers: dict[str, Handler] = {}
    for control in ("stop", "start", "switch_on", "switch_off"):
        handlers[f"voice.control.{control}"] = _directive("control", "control", control)
    for direction in ("up", "down", "left", "right"):
        handlers[f"voice.navigate.{direction}"] = _directive("navigate", "direction", direction)
    for answer in ("affirm", "reject"):
        handlers[f"voice.dialog.{answer}"] = _directive("dialog", "answer", answer)
    return handlers


class VoiceCommandExecutor:
    """Registry ``action_type -> handler``; exact names only, an unknown type is denied, never guessed."""

    def __init__(self, handlers: Mapping[str, Handler] | None = None) -> None:
        self._handlers: dict[str, Handler] = dict(default_handlers() if handlers is None else handlers)

    def register(self, action_type: str, handler: Handler) -> None:
        if not action_type or action_type in self._handlers:
            raise ValueError("action type must be new and non-empty")
        self._handlers[action_type] = handler

    def supports(self, action_type: str | None) -> bool:
        return isinstance(action_type, str) and action_type in self._handlers

    def execute(self, invocation: VoiceCommandInvocation, *, authorize: Authorize) -> VoiceCommandExecution:
        action_type = invocation.action_type
        handler = self._handlers.get(action_type) if isinstance(action_type, str) else None
        if handler is None:
            return VoiceCommandExecution(False, str(action_type), "executor.unknown_action_type")
        try:
            authorized = authorize() is True
        except Exception:
            authorized = False
        if not authorized:
            return VoiceCommandExecution(False, action_type, "executor.not_authorized")
        try:
            effect = handler(invocation)
        except Exception:
            return VoiceCommandExecution(False, action_type, "executor.handler_failed")
        if not isinstance(effect, Mapping):
            return VoiceCommandExecution(False, action_type, "executor.handler_failed")
        return VoiceCommandExecution(True, action_type, None, dict(effect))


@dataclass(frozen=True)
class PendingVoiceCommand:
    """A confirmation bound to principal and concrete action (kept in memory only)."""

    confirmation_id: str
    tenant_id: str
    subject: str
    action_type: str
    command: Any = field(repr=False)
    profile: str
    field: str
    expires_at: float

    def invocation(self) -> VoiceCommandInvocation:
        return VoiceCommandInvocation(
            action_type=self.action_type,
            command=self.command,
            profile=self.profile,
            field=self.field,
            principal=VoicePrincipal(tenant_id=self.tenant_id, subject=self.subject),
        )


class ConfirmationError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def confirmation_ttl_seconds(environ: Mapping[str, str] | None = None) -> float:
    env = os.environ if environ is None else environ
    raw = env.get("VOICE_AUDIO_DECISION_CONFIRM_TTL_SECONDS")
    try:
        value = float(raw) if raw else DEFAULT_CONFIRMATION_TTL_SECONDS
    except ValueError:
        value = DEFAULT_CONFIRMATION_TTL_SECONDS
    if value != value:  # NaN
        value = DEFAULT_CONFIRMATION_TTL_SECONDS
    return max(_MIN_TTL_SECONDS, min(value, _MAX_TTL_SECONDS))


class VoiceCommandConfirmationStore:
    """Short-lived, single-use confirmations. Any use - even a denied one - consumes the id."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        max_pending: int = _MAX_PENDING_CONFIRMATIONS,
    ) -> None:
        self._clock = clock
        self._max_pending = max(1, max_pending)
        self._pending: dict[str, PendingVoiceCommand] = {}
        self._used: dict[str, float] = {}  # id -> original expiry, to tell replays apart
        self._lock = threading.Lock()

    def issue(
        self,
        principal: VoicePrincipal,
        *,
        action_type: str,
        command: Any,
        profile: str,
        field_name: str,
        ttl_seconds: float,
    ) -> PendingVoiceCommand:
        if not principal.tenant_id or not principal.subject:
            raise ConfirmationError("confirmation.principal_required")
        with self._lock:
            now = self._clock()
            self._purge(now)
            if len(self._pending) >= self._max_pending:
                raise ConfirmationError("confirmation.capacity_exceeded")
            pending = PendingVoiceCommand(
                confirmation_id=f"vcc-{secrets.token_urlsafe(24)}",
                tenant_id=principal.tenant_id,
                subject=principal.subject,
                action_type=action_type,
                command=command,
                profile=profile,
                field=field_name,
                expires_at=now + ttl_seconds,
            )
            self._pending[pending.confirmation_id] = pending
            return pending

    def consume(self, confirmation_id: str, principal: VoicePrincipal, *, action_type: str) -> PendingVoiceCommand:
        """Remove and return the confirmation, or raise ``ConfirmationError`` (fail-closed)."""
        with self._lock:
            now = self._clock()
            pending = self._pending.pop(confirmation_id, None)
            if pending is None:
                used = confirmation_id in self._used
                self._purge(now)
                raise ConfirmationError("confirmation.already_used" if used else "confirmation.unknown")
            self._used[confirmation_id] = pending.expires_at
            self._purge(now)
        if now >= pending.expires_at:
            raise ConfirmationError("confirmation.expired")
        if pending.tenant_id != principal.tenant_id or pending.subject != principal.subject:
            raise ConfirmationError("confirmation.foreign_binding")
        if pending.action_type != action_type:
            raise ConfirmationError("confirmation.action_mismatch")
        return pending

    def pending_count(self) -> int:
        with self._lock:
            self._purge(self._clock())
            return len(self._pending)

    def _purge(self, now: float) -> None:
        for key in [key for key, item in self._pending.items() if now >= item.expires_at]:
            self._used[key] = self._pending.pop(key).expires_at
        # Keep replay markers for one more TTL window, then forget them.
        for key in [key for key, expiry in self._used.items() if now >= expiry + _MAX_TTL_SECONDS]:
            del self._used[key]


@dataclass(frozen=True)
class AudioDecisionDispatch:
    response: dict[str, Any]
    audit: dict[str, Any]
    execution: VoiceCommandExecution | None = None


def dispatch_audio_decision(
    result: AudioDecisionCommandResult,
    *,
    principal: VoicePrincipal,
    authorize: Authorize,
    executor: VoiceCommandExecutor,
    confirmations: VoiceCommandConfirmationStore,
    ttl_seconds: float,
) -> AudioDecisionDispatch:
    response = result.as_response()
    audit = result.as_audit_dict()
    hub = result.hub
    response["execution"] = None
    response["confirmation"] = None
    if hub.action not in {HubAction.ACT, HubAction.CONFIRM}:
        return AudioDecisionDispatch(response=response, audit=audit)

    action = result.action
    denial: str | None = None
    if hub.grants_permission is not False:
        denial = "executor.permission_claim"
    elif action is None or result.policy is None:
        denial = "executor.missing_action"
    elif not executor.supports(action.action_type):
        denial = "executor.unknown_action_type"
    elif hub.action is HubAction.ACT and result.policy.verdict is not PolicyVerdict.ALLOW:
        denial = "executor.not_policy_allowed"
    if denial is not None:
        return _denied(response, audit, action.action_type if action is not None else None, denial)
    assert action is not None

    if hub.action is HubAction.ACT:
        execution = executor.execute(
            VoiceCommandInvocation(
                action_type=action.action_type,
                command=hub.value,
                profile=result.profile,
                field=hub.field,
                principal=principal,
            ),
            authorize=authorize,
        )
        if not execution.executed:
            return _denied(response, audit, action.action_type, execution.error_code or "executor.denied")
        response["execution"] = execution.as_response()
        audit["execution"] = execution.as_audit_dict()
        return AudioDecisionDispatch(response=response, audit=audit, execution=execution)

    try:
        pending = confirmations.issue(
            principal,
            action_type=action.action_type,
            command=hub.value,
            profile=result.profile,
            field_name=hub.field,
            ttl_seconds=ttl_seconds,
        )
    except ConfirmationError as exc:
        return _denied(response, audit, action.action_type, exc.code)
    response["confirmation"] = {
        "confirmation_id": pending.confirmation_id,
        "action_type": pending.action_type,
        "expires_in_seconds": ttl_seconds,
        "confirm_route": CONFIRM_ROUTE,
        "single_use": True,
        "grants_permission": False,
    }
    audit["confirmation"] = {"issued": True, "ttl_seconds": ttl_seconds}
    return AudioDecisionDispatch(response=response, audit=audit)


def _denied(
    response: dict[str, Any], audit: dict[str, Any], action_type: str | None, error_code: str
) -> AudioDecisionDispatch:
    execution = VoiceCommandExecution(False, str(action_type), error_code)
    response.update(
        {
            "hub_action": HubAction.DENY.value,
            "execution": execution.as_response(),
            "confirmation": None,
            "error_code": error_code,
            "grants_permission": False,
        }
    )
    audit["execution"] = execution.as_audit_dict()
    return AudioDecisionDispatch(response=response, audit=audit, execution=execution)


def confirm_voice_command(
    confirmation_id: str,
    *,
    principal: VoicePrincipal,
    action_type: str,
    confirmed: bool,
    authorize: Authorize,
    executor: VoiceCommandExecutor,
    confirmations: VoiceCommandConfirmationStore,
) -> VoiceCommandExecution:
    """Consume a confirmation; only ``confirmed is True`` with a valid binding executes."""
    try:
        pending = confirmations.consume(confirmation_id, principal, action_type=action_type)
    except ConfirmationError as exc:
        return VoiceCommandExecution(False, action_type, exc.code)
    if confirmed is not True:
        return VoiceCommandExecution(False, pending.action_type, "confirmation.rejected")
    return executor.execute(pending.invocation(), authorize=authorize)


_executor = VoiceCommandExecutor()
_confirmations = VoiceCommandConfirmationStore()


def get_voice_command_executor() -> VoiceCommandExecutor:
    return _executor


def get_voice_command_confirmation_store() -> VoiceCommandConfirmationStore:
    return _confirmations
