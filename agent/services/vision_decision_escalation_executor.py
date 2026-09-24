"""Executor for escalated VisionDecision fields: chat completion, larger model or a human.

``gate_vision_decision`` marks every uncertain field (server ``abstained``, ``abstain: true``, missing
flag or Ananta's local threshold check) as ``escalate``; its value is never taken over. This module
carries those escalations out, bounded and fail-closed:

* ``chat_completion`` / ``larger_model``: one OpenAI-compatible ``POST /v1/chat/completions`` per
  target with the same image(s) and a ``json_schema`` covering only the escalated fields (at most
  :data:`MAX_CHAT_FIELDS`). The answer is validated against the field schema; it is an unscored
  *suggestion* that the hub policy can at most send to ``confirm`` (rule V7), never ``act`` on.
* ``human`` - and every machine escalation that is not configured, fails, times out, answers 4xx/5xx,
  answers garbage or overflows the field bound: a short-lived, single-use review ticket bound to
  tenant, subject, task and field (the voice confirmation store). Only an explicit human answer
  through the resolve route consumes it.

A failure never becomes a value and never a default label. Images, prompts, answers and keys are
never logged; audit projections carry field names, targets, statuses, reasons and scores only.
"""
from __future__ import annotations

import base64
import http.client
import json
import logging
import os
import socket
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from agent.services.audio_decision_command_executor import ConfirmationError, VoiceCommandConfirmationStore
from agent.services.vision_decision_hub_gate import EscalationTarget, VisionEscalation
from agent.services.vision_decision_provider import (
    HttpClientTransport,
    ImageRejected,
    VisionDecisionConfig,
    VisionDecisionConfigurationError,
    VisionDecisionTransport,
    VisionField,
    prepare_image,
)
from agent.services.vision_decision_task_policy import VisionTask
from agent.services.voice_governance_domain import VoicePrincipal

_log = logging.getLogger(__name__)

CHAT_PATH = "/v1/chat/completions"
RESOLVE_ROUTE = "/v1/vision/decision/escalations/resolve"
MAX_CHAT_FIELDS = 8
MAX_CHAT_TOKENS = 256
DEFAULT_TICKET_TTL_SECONDS = 600.0
_MIN_TICKET_TTL_SECONDS = 30.0
_MAX_TICKET_TTL_SECONDS = 3600.0
_MAX_PENDING_TICKETS = 256


def escalation_action_type(task_id: str, field_name: str) -> str:
    """The concrete action a review ticket is bound to."""
    return f"vision.escalation.{task_id}.{field_name}"


# ---------------------------------------------------------------------------------------------------------
# Configuration


def _as_bool(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class ChatTargetConfig:
    url: str
    model: str | None = None
    api_key: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class VisionEscalationConfig:
    chat: ChatTargetConfig | None = None
    larger_model: ChatTargetConfig | None = None
    timeout_ms: int = 30_000
    ticket_ttl_seconds: float = DEFAULT_TICKET_TTL_SECONDS

    @classmethod
    def from_env(cls, decision: VisionDecisionConfig, environ: Mapping[str, str] | None = None) -> "VisionEscalationConfig":
        """Read only when VisionDecision is enabled; raises ``VisionDecisionConfigurationError``."""
        env = os.environ if environ is None else environ

        def target(prefix: str, *, default_url: str | None) -> ChatTargetConfig | None:
            url = str(env.get(f"{prefix}_URL") or default_url or "").strip()
            if not url:
                return None
            # Reuse the decision URL rules (scheme, no path/credentials, https for non-local hosts).
            VisionDecisionConfig(enabled=True, url=url).validate()
            model = str(env.get(f"{prefix}_MODEL") or "").strip() or None
            # The decision key only goes to the decision server itself, never to another host.
            same_server = url.rstrip("/") == decision.url.rstrip("/")
            return ChatTargetConfig(url=url, model=model, api_key=decision.api_key if same_server else None)

        chat = None
        if _as_bool(env.get("VISION_DECISION_ESCALATION_CHAT")):
            # Default: the fork's llama-server also serves an OpenAI-compatible chat with the same mmproj.
            chat = target("VISION_DECISION_ESCALATION_CHAT", default_url=decision.url)
        larger = target("VISION_DECISION_ESCALATION_LARGER", default_url=None)
        raw_ttl = str(env.get("VISION_DECISION_ESCALATION_TTL_SECONDS") or DEFAULT_TICKET_TTL_SECONDS).strip()
        try:
            ttl = float(raw_ttl)
        except ValueError as exc:
            raise VisionDecisionConfigurationError("VISION_DECISION_ESCALATION_TTL_SECONDS must be a number") from exc
        if not _MIN_TICKET_TTL_SECONDS <= ttl <= _MAX_TICKET_TTL_SECONDS:
            raise VisionDecisionConfigurationError("VISION_DECISION_ESCALATION_TTL_SECONDS must be between 30 and 3600")
        return cls(chat=chat, larger_model=larger, timeout_ms=decision.timeout_ms, ticket_ttl_seconds=ttl)


# ---------------------------------------------------------------------------------------------------------
# Chat-completion handler


@dataclass(frozen=True)
class ChatEscalationAnswer:
    ok: bool
    values: Mapping[str, Any] = field(default_factory=dict, repr=False)
    error_code: str | None = None
    http_status: int | None = None
    model: str | None = None


def _json_schema(spec: VisionField) -> dict[str, Any]:
    if spec.type == "enum":
        return {"type": "string", "enum": list(spec.choices)}
    if spec.type == "boolean":
        return {"type": "boolean"}
    kind = "integer" if spec.type == "integer" else "number"
    return {"type": kind, "minimum": spec.minimum, "maximum": spec.maximum}


class ChatCompletionEscalationHandler:
    """Asks an OpenAI-compatible VLM chat for the escalated fields only; fail-closed on anything odd."""

    def __init__(
        self,
        target: ChatTargetConfig,
        *,
        timeout_ms: int,
        max_image_bytes: int,
        max_image_side: int,
        transport: VisionDecisionTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._target = target
        self._timeout_s = timeout_ms / 1000.0
        self._max_image_bytes = max_image_bytes
        self._max_image_side = max_image_side
        self._transport = transport or HttpClientTransport()
        self._clock = clock

    def answer(
        self,
        task: VisionTask,
        field_names: Sequence[str],
        *,
        images: Sequence[bytes],
        text: str = "",
        deadline_monotonic: float | None = None,
    ) -> ChatEscalationAnswer:
        try:
            return self._answer(task, field_names, images=images, text=text, deadline_monotonic=deadline_monotonic)
        except Exception:  # a handler bug never becomes a value
            _log.exception("vision escalation handler failed")
            return ChatEscalationAnswer(False, error_code="provider_error")

    def _answer(
        self,
        task: VisionTask,
        field_names: Sequence[str],
        *,
        images: Sequence[bytes],
        text: str,
        deadline_monotonic: float | None,
    ) -> ChatEscalationAnswer:
        specs = [task.fields[name].spec for name in field_names]
        timeout_s = self._timeout_s
        if deadline_monotonic is not None:
            timeout_s = min(timeout_s, deadline_monotonic - self._clock())
            if timeout_s <= 0.0:
                return ChatEscalationAnswer(False, error_code="deadline_exceeded")
        parts: list[dict[str, Any]] = []
        for content in images:
            try:
                png = prepare_image(content, max_bytes=self._max_image_bytes, max_side=self._max_image_side)
            except ImageRejected as exc:
                return ChatEscalationAnswer(False, error_code=exc.code)
            parts.append({"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(png).decode("ascii")}})
        questions = "\n".join(f"- {spec.name}: {spec.description}" for spec in specs)
        prompt = "\n\n".join(
            item
            for item in (
                task.instructions,
                text.strip(),
                "Answer every question about the image(s) as a JSON object with exactly these keys:\n" + questions,
            )
            if item
        )
        parts.append({"type": "text", "text": prompt})
        request: dict[str, Any] = {
            "messages": [{"role": "user", "content": parts}],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "vision_escalation",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "properties": {spec.name: _json_schema(spec) for spec in specs},
                        "required": [spec.name for spec in specs],
                        "additionalProperties": False,
                    },
                },
            },
            "temperature": 0,
            "max_tokens": MAX_CHAT_TOKENS,
            "stream": False,
        }
        if self._target.model:
            request["model"] = self._target.model
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self._target.api_key:
            headers["Authorization"] = f"Bearer {self._target.api_key}"
        try:
            status, body = self._transport.request(
                "POST",
                self._target.url.rstrip("/") + CHAT_PATH,
                body=json.dumps(request).encode("utf-8"),
                headers=headers,
                timeout_s=timeout_s,
            )
        except (socket.timeout, TimeoutError):
            return ChatEscalationAnswer(False, error_code="deadline_exceeded")
        except (OSError, http.client.HTTPException):
            return ChatEscalationAnswer(False, error_code="unavailable")
        if status != 200:
            return ChatEscalationAnswer(False, error_code=f"http_{status}", http_status=status)
        try:
            payload = json.loads(body)
            content = payload["choices"][0]["message"]["content"]
            values = json.loads(content) if isinstance(content, str) else None
        except (ValueError, UnicodeError, KeyError, IndexError, TypeError):
            return ChatEscalationAnswer(False, error_code="bad_response", http_status=status)
        if not isinstance(values, dict) or set(values) != {spec.name for spec in specs}:
            return ChatEscalationAnswer(False, error_code="bad_response", http_status=status)
        if not all(spec.allowed(values[spec.name]) for spec in specs):
            return ChatEscalationAnswer(False, error_code="bad_response", http_status=status)
        model = payload.get("model") if isinstance(payload.get("model"), str) else None
        return ChatEscalationAnswer(True, values=values, http_status=status, model=model)


# ---------------------------------------------------------------------------------------------------------
# Executor


@dataclass(frozen=True)
class VisionEscalationResult:
    escalation: VisionEscalation
    handled_by: EscalationTarget
    status: str  # "answered" | "awaiting_human" | "failed"
    reasons: tuple[str, ...]
    error_code: str | None = None
    suggestion: Any = field(default=None, repr=False)  # only for "answered"; unscored
    answered_by_model: str | None = None
    escalation_id: str | None = field(default=None, repr=False)
    expires_in_seconds: float | None = None

    def as_response(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            **self.escalation.as_dict(),
            "reasons": list(self.reasons),
            "handled_by": self.handled_by.value,
            "status": self.status,
            "error_code": self.error_code,
            "grants_permission": False,
        }
        if self.escalation_id is not None:
            out.update(
                {
                    "escalation_id": self.escalation_id,
                    "expires_in_seconds": self.expires_in_seconds,
                    "resolve_route": RESOLVE_ROUTE,
                    "single_use": True,
                }
            )
        return out

    def as_audit_dict(self) -> dict[str, Any]:
        """No suggestion, no ticket id."""
        return {
            **self.escalation.as_dict(),
            "reasons": list(self.reasons),
            "handled_by": self.handled_by.value,
            "status": self.status,
            "error_code": self.error_code,
            "answered_by_model": self.answered_by_model,
        }


class VisionEscalationExecutor:
    """Runs escalations; a machine target that is missing or fails falls back to a human ticket."""

    def __init__(
        self,
        handlers: Mapping[EscalationTarget, ChatCompletionEscalationHandler] | None = None,
        *,
        tickets: VoiceCommandConfirmationStore | None = None,
        ticket_ttl_seconds: float = DEFAULT_TICKET_TTL_SECONDS,
    ) -> None:
        self._handlers = dict(handlers or {})
        self._tickets = tickets or VoiceCommandConfirmationStore(max_pending=_MAX_PENDING_TICKETS)
        self._ticket_ttl_seconds = ticket_ttl_seconds

    @property
    def tickets(self) -> VoiceCommandConfirmationStore:
        return self._tickets

    def escalate(
        self,
        task: VisionTask,
        escalations: Sequence[VisionEscalation],
        *,
        principal: VoicePrincipal,
        images: Sequence[bytes],
        text: str = "",
        deadline_monotonic: float | None = None,
    ) -> dict[str, VisionEscalationResult]:
        results: dict[str, VisionEscalationResult] = {}
        to_human: list[tuple[VisionEscalation, tuple[str, ...], str | None]] = []
        for target in (EscalationTarget.LARGER_MODEL, EscalationTarget.CHAT_COMPLETION):
            items = [e for e in escalations if e.target is target and e.field in task.fields]
            if not items:
                continue
            handler = self._handlers.get(target)
            if handler is None:
                to_human += [(e, (f"{target.value}_unavailable",), None) for e in items]
                continue
            batch, overflow = items[:MAX_CHAT_FIELDS], items[MAX_CHAT_FIELDS:]
            to_human += [(e, ("escalation_overflow",), None) for e in overflow]
            answer = handler.answer(
                task, [e.field for e in batch], images=images, text=text, deadline_monotonic=deadline_monotonic
            )
            if not answer.ok:
                to_human += [(e, (f"{target.value}_failed",), answer.error_code) for e in batch]
                continue
            for e in batch:
                results[e.field] = VisionEscalationResult(
                    escalation=e,
                    handled_by=target,
                    status="answered",
                    reasons=e.reasons,
                    suggestion=answer.values[e.field],
                    answered_by_model=answer.model,
                )
        to_human += [(e, (), None) for e in escalations if e.target is EscalationTarget.HUMAN and e.field in task.fields]
        for e, extra, error_code in to_human:
            results[e.field] = self._ticket(task, e, principal, reasons=e.reasons + extra, error_code=error_code)
        return results

    def _ticket(
        self,
        task: VisionTask,
        escalation: VisionEscalation,
        principal: VoicePrincipal,
        *,
        reasons: tuple[str, ...],
        error_code: str | None,
    ) -> VisionEscalationResult:
        try:
            pending = self._tickets.issue(
                principal,
                action_type=escalation_action_type(task.task_id, escalation.field),
                command=None,
                profile=task.task_id,
                field_name=escalation.field,
                ttl_seconds=self._ticket_ttl_seconds,
            )
        except ConfirmationError as exc:
            return VisionEscalationResult(escalation, EscalationTarget.HUMAN, "failed", reasons, exc.code)
        return VisionEscalationResult(
            escalation,
            EscalationTarget.HUMAN,
            "awaiting_human",
            reasons,
            error_code,
            escalation_id=pending.confirmation_id,
            expires_in_seconds=self._ticket_ttl_seconds,
        )


def build_escalation_executor(
    decision: VisionDecisionConfig,
    escalation: VisionEscalationConfig,
    *,
    tickets: VoiceCommandConfirmationStore | None = None,
    transport: VisionDecisionTransport | None = None,
) -> VisionEscalationExecutor:
    handlers: dict[EscalationTarget, ChatCompletionEscalationHandler] = {}
    for target, cfg in ((EscalationTarget.CHAT_COMPLETION, escalation.chat), (EscalationTarget.LARGER_MODEL, escalation.larger_model)):
        if cfg is not None:
            handlers[target] = ChatCompletionEscalationHandler(
                cfg,
                timeout_ms=escalation.timeout_ms,
                max_image_bytes=decision.max_image_bytes,
                max_image_side=decision.max_image_side,
                transport=transport,
            )
    return VisionEscalationExecutor(handlers, tickets=tickets, ticket_ttl_seconds=escalation.ticket_ttl_seconds)
