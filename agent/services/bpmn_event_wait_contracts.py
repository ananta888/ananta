"""Closed Hub contracts for bounded intermediate BPMN event catches."""

from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Protocol

from agent.services.identity_validation import require_canonical_identity
from agent.services.workflow_runtime._serialization import canonical_json, contains_sensitive_keys, sha256_json
from agent.services.workflow_runtime.events import CanonicalWorkflowEvent


class EventWaitError(ValueError):
    """A stable, machine-readable contract rejection; no interactive recovery."""


class EventWaitConflict(EventWaitError):
    """An immutable binding or idempotency key was reused with different data."""


def identity(value: str, name: str) -> None:
    require_canonical_identity(value, field_name=name)


def seconds(value: float, name: str, *, allow_zero: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EventWaitError(f"bpmn_wait_{name}_invalid")
    try:
        numeric = float(value)
    except OverflowError as exc:
        raise EventWaitError(f"bpmn_wait_{name}_invalid") from exc
    if not math.isfinite(numeric) or numeric < 0 or (numeric == 0 and not allow_zero):
        raise EventWaitError(f"bpmn_wait_{name}_invalid")
    return numeric


@dataclass(frozen=True)
class WaitRunBinding:
    tenant_id: str
    project_id: str
    workflow_id: str
    run_id: str
    definition_revision: str
    plan_hash: str
    policy_version: str

    def __post_init__(self) -> None:
        for name, value in asdict(self).items():
            identity(value, name)
        if not re.fullmatch(r"[a-f0-9]{64}", self.plan_hash):
            raise EventWaitError("bpmn_wait_plan_hash_invalid")


@dataclass(frozen=True)
class CatchActivation:
    element_id: str
    activation_id: str

    def __post_init__(self) -> None:
        identity(self.element_id, "element_id")
        identity(self.activation_id, "activation_id")

    @property
    def key(self) -> str:
        return sha256_json(asdict(self))


@dataclass(frozen=True)
class TimerSpec:
    """Exactly one duration in seconds or absolute UTC epoch timestamp."""

    duration_seconds: float | None = None
    due_at: float | None = None

    def __post_init__(self) -> None:
        if (self.duration_seconds is None) == (self.due_at is None):
            raise EventWaitError("bpmn_wait_timer_form_invalid")
        if self.duration_seconds is not None:
            seconds(self.duration_seconds, "duration", allow_zero=True)
        if self.due_at is not None:
            seconds(self.due_at, "due_at")

    @classmethod
    def at(cls, value: str) -> TimerSpec:
        """Reject ambiguous local dates; retain the normalized UTC instant."""
        try:
            if not isinstance(value, str) or len(value) > 80 or "T" not in value:
                raise ValueError
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                raise ValueError
            return cls(due_at=parsed.timestamp())
        except (ValueError, OverflowError) as exc:
            raise EventWaitError("bpmn_wait_timezone_date_required") from exc


@dataclass(frozen=True)
class MessageContract:
    name: str
    correlation_key: str
    schema_id: str

    def __post_init__(self) -> None:
        for name, value in asdict(self).items():
            identity(value, name)


@dataclass(frozen=True)
class InboundMessage:
    run: WaitRunBinding
    target: CatchActivation
    contract: MessageContract
    message_id: str
    sent_at: float
    expires_at: float
    payload: dict[str, Any]

    def __post_init__(self) -> None:
        identity(self.message_id, "message_id")
        seconds(self.sent_at, "sent_at")
        seconds(self.expires_at, "expires_at")
        if self.expires_at <= self.sent_at:
            raise EventWaitError("bpmn_wait_message_ttl_invalid")


class MessageAuthorizer(Protocol):
    def authorize(self, message: InboundMessage) -> None:
        """Raise unless the authenticated principal may send this exact envelope.

        Resolve active run, project, definition, target activation and message
        contract from Hub authority, including for early deliveries.
        """


class MessagePayloadValidator(Protocol):
    def validate(self, schema_id: str, payload: dict[str, Any]) -> None:
        """Raise for unknown/changed schemas or invalid payloads. Never execute."""


@dataclass(frozen=True)
class EarlyMessagePolicy:
    ttl_seconds: float
    max_buffered: int

    def __post_init__(self) -> None:
        seconds(self.ttl_seconds, "buffer_ttl")
        if type(self.max_buffered) is not int or not 1 <= self.max_buffered <= 1024:
            raise EventWaitError("bpmn_wait_buffer_limit_invalid")


@dataclass(frozen=True)
class WaitLimits:
    max_wait_seconds: float = 86400.0
    max_message_ttl: float = 3600.0
    max_activations: int = 128
    max_messages: int = 256
    max_payload_bytes: int = 16384
    max_state_bytes: int = 1048576
    cas_attempts: int = 8

    def __post_init__(self) -> None:
        seconds(self.max_wait_seconds, "max_wait_seconds")
        seconds(self.max_message_ttl, "max_message_ttl")
        for name, cap in (
            ("max_activations", 4096),
            ("max_messages", 4096),
            ("max_payload_bytes", 1048576),
            ("max_state_bytes", 16777216),
            ("cas_attempts", 32),
        ):
            value = getattr(self, name)
            if type(value) is not int or not 1 <= value <= cap:
                raise EventWaitError(f"bpmn_wait_{name}_invalid")


def payload_copy(payload: dict[str, Any], *, max_bytes: int) -> dict[str, Any]:
    """Bound pure JSON before callbacks; never silently redact or coerce data."""
    budget = [4096]

    def visit(value: Any, depth: int = 0) -> None:
        budget[0] -= 1
        if depth > 16 or budget[0] < 0:
            raise EventWaitError("bpmn_wait_payload_complexity_exceeded")
        if type(value) is dict:
            for key, child in value.items():
                if type(key) is not str:
                    raise EventWaitError("bpmn_wait_payload_json_required")
                visit(child, depth + 1)
        elif type(value) is list:
            for child in value:
                visit(child, depth + 1)
        elif value is not None and type(value) not in (str, bool, int, float):
            raise EventWaitError("bpmn_wait_payload_json_required")

    if type(payload) is not dict:
        raise EventWaitError("bpmn_wait_payload_object_required")
    visit(payload)
    try:
        encoded = canonical_json(payload)
        size = len(encoded.encode("utf-8"))
    except (ValueError, UnicodeError) as exc:
        raise EventWaitError("bpmn_wait_payload_invalid") from exc
    if size > max_bytes:
        raise EventWaitError("bpmn_wait_payload_too_large")
    if contains_sensitive_keys(payload):
        raise EventWaitError("bpmn_wait_embedded_secret_denied")
    return json.loads(encoded)


@dataclass(frozen=True)
class WaitView:
    target: CatchActivation
    kind: str
    status: str
    created_at: float
    expires_at: float
    due_at: float | None
    wakeup_id: str


@dataclass(frozen=True)
class MessageDelivery:
    message_id: str
    status: str
    target: CatchActivation


@dataclass(frozen=True)
class Wakeup:
    run: WaitRunBinding
    target: CatchActivation
    wakeup_id: str
    kind: str
    ready_at: float
    consumed_at: float | None
    message_id: str | None
    payload: dict[str, Any] | None

    def to_event(self) -> CanonicalWorkflowEvent:
        """Stable replay projection for the existing EventStore; data stays private."""
        if self.consumed_at is None:
            raise EventWaitError("bpmn_wait_wakeup_not_consumed")
        return CanonicalWorkflowEvent.build(
            tenant_id=self.run.tenant_id,
            workflow_id=self.run.workflow_id,
            run_id=self.run.run_id,
            step_id=self.target.element_id,
            event_type="workflow.bpmn.catch.consumed",
            actor="hub",
            correlation_id=self.run.run_id,
            causation_id=self.wakeup_id,
            event_id=self.wakeup_id,
            dedupe_key=self.wakeup_id,
            occurred_at=self.consumed_at,
            payload={
                "kind": self.kind,
                "activation_id": self.target.activation_id,
                "project_id": self.run.project_id,
                "definition_revision": self.run.definition_revision,
                "plan_hash": self.run.plan_hash,
                "message_id": self.message_id,
                "payload_digest": sha256_json(self.payload),
            },
        )
