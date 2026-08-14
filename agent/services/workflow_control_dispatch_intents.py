"""Immutable Hub dispatch-intent contract for restart-safe runtime mutations.

The intent is an outbox record owned by the Hub.  It contains an already
authorized mutation, while the infrastructure adapter remains responsible only
for talking to the selected durable runtime.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Protocol

from agent.services.workflow_backend import WorkflowRequest
from agent.services.workflow_backend_durable_run_adapter import (
    DURABLE_RUN_START_SCHEMA,
)
from agent.services.workflow_control_bindings import WorkflowControlRunBinding
from agent.services.workflow_runtime._serialization import canonical_json
from agent.services.workflow_runtime.commands import SignedWorkflowCommand

WORKFLOW_CONTROL_DISPATCH_PAYLOAD_SCHEMA = "ananta.workflow-control-dispatch-payload.v1"
DISPATCH_KIND_COMMAND = "command"
DISPATCH_KIND_START = "start"
DISPATCH_STATE_READY = "ready"
DISPATCH_STATE_DISPATCHING = "dispatching"
DISPATCH_STATE_OBSERVATION_PENDING = "observation_pending"
DISPATCH_STATE_COMPLETED = "completed"
DISPATCH_STATE_REJECTED = "rejected"

_KINDS = frozenset({DISPATCH_KIND_COMMAND, DISPATCH_KIND_START})
_STATES = frozenset(
    {
        DISPATCH_STATE_READY,
        DISPATCH_STATE_DISPATCHING,
        DISPATCH_STATE_OBSERVATION_PENDING,
        DISPATCH_STATE_COMPLETED,
        DISPATCH_STATE_REJECTED,
    }
)
_RESUMABLE_STATES = frozenset({DISPATCH_STATE_READY, DISPATCH_STATE_OBSERVATION_PENDING})
_IDENTITY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,255}$")
_MAX_COMMAND_PAYLOAD_BYTES = 80_000
_MAX_START_PAYLOAD_BYTES = 524_288


class WorkflowControlDispatchIntentError(RuntimeError):
    """Stable fail-closed intent validation or persistence failure."""


@dataclass(frozen=True)
class WorkflowControlDispatchIntent:
    intent_id: str
    kind: str
    tenant_id: str
    workflow_id: str
    run_id: str
    payload: dict[str, Any]
    state: str = DISPATCH_STATE_READY
    dispatch_from_state: str = DISPATCH_STATE_READY
    acknowledgement_revision: int = 0
    acknowledgement_status: str = ""
    attempt_count: int = 0
    available_at: float = 0.0
    lease_owner: str = ""
    lease_expires_at: float = 0.0
    last_error: str = ""
    revision: int = 1

    def __post_init__(self) -> None:
        _identity(self.intent_id, "intent_id")
        _identity(self.tenant_id, "tenant_id")
        _identity(self.workflow_id, "workflow_id")
        _identity(self.run_id, "run_id")
        if self.kind not in _KINDS:
            raise WorkflowControlDispatchIntentError("workflow_control_dispatch_kind_invalid")
        if self.state not in _STATES or self.dispatch_from_state not in _RESUMABLE_STATES:
            raise WorkflowControlDispatchIntentError("workflow_control_dispatch_state_invalid")
        for value, reason in (
            (self.acknowledgement_revision, "acknowledgement_revision"),
            (self.attempt_count, "attempt_count"),
            (self.revision, "revision"),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise WorkflowControlDispatchIntentError(f"workflow_control_dispatch_{reason}_invalid")
        if self.revision < 1:
            raise WorkflowControlDispatchIntentError("workflow_control_dispatch_revision_invalid")
        _bounded_text(self.acknowledgement_status, 64, "acknowledgement_status", empty=True)
        _bounded_text(self.lease_owner, 256, "lease_owner", empty=True)
        _bounded_text(self.last_error, 256, "last_error", empty=True)
        for value in (self.available_at, self.lease_expires_at):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                raise WorkflowControlDispatchIntentError("workflow_control_dispatch_timestamp_invalid")
        _validate_payload(
            self.payload,
            kind=self.kind,
            tenant_id=self.tenant_id,
            workflow_id=self.workflow_id,
            run_id=self.run_id,
        )

    @property
    def phase(self) -> str:
        return self.dispatch_from_state if self.state == DISPATCH_STATE_DISPATCHING else self.state

    @property
    def command(self) -> SignedWorkflowCommand:
        if self.kind != DISPATCH_KIND_COMMAND:
            raise WorkflowControlDispatchIntentError("workflow_control_dispatch_command_kind_required")
        return SignedWorkflowCommand.from_mapping(dict(self.payload["command"]))

    @property
    def start_command(self) -> dict[str, Any]:
        if self.kind != DISPATCH_KIND_START:
            raise WorkflowControlDispatchIntentError("workflow_control_dispatch_start_kind_required")
        return dict(self.payload["start"])

    @property
    def start_request_id(self) -> str:
        if self.kind != DISPATCH_KIND_START:
            raise WorkflowControlDispatchIntentError("workflow_control_dispatch_start_kind_required")
        return str(self.payload["request_id"])


class WorkflowControlDispatchIntentStore(Protocol):
    """Small persistence port; production implementations are transactional."""

    def stage_command(
        self,
        *,
        binding: WorkflowControlRunBinding,
        command: SignedWorkflowCommand,
    ) -> WorkflowControlDispatchIntent: ...

    def stage_start(
        self,
        *,
        binding: WorkflowControlRunBinding,
        start_command: dict[str, Any],
        request_id: str,
        pending_status: dict[str, Any],
    ) -> WorkflowControlDispatchIntent: ...

    def get_active(self, workflow_id: str) -> WorkflowControlDispatchIntent | None: ...

    def get(self, intent_id: str) -> WorkflowControlDispatchIntent | None: ...

    def claim(
        self,
        intent_id: str,
        *,
        owner_id: str,
        lease_seconds: float,
    ) -> WorkflowControlDispatchIntent | None: ...

    def claim_due(
        self,
        *,
        owner_id: str,
        lease_seconds: float,
        limit: int,
    ) -> tuple[WorkflowControlDispatchIntent, ...]: ...

    def acknowledge(
        self,
        intent_id: str,
        *,
        owner_id: str,
        acknowledgement_revision: int = 0,
        acknowledgement_status: str = "",
    ) -> WorkflowControlDispatchIntent: ...

    def release(
        self,
        intent_id: str,
        *,
        owner_id: str,
        reason_code: str,
        retry_at: float,
    ) -> None: ...

    def complete(
        self,
        intent_id: str,
        *,
        owner_id: str,
        status: dict[str, Any],
    ) -> None: ...

    def reject(
        self,
        intent_id: str,
        *,
        owner_id: str,
        reason_code: str,
        status: dict[str, Any] | None = None,
    ) -> None: ...


def command_intent_payload(command: SignedWorkflowCommand) -> dict[str, Any]:
    payload = {
        "schema": WORKFLOW_CONTROL_DISPATCH_PAYLOAD_SCHEMA,
        "command": command.to_dict(),
    }
    _validate_payload(
        payload,
        kind=DISPATCH_KIND_COMMAND,
        tenant_id=command.tenant_id,
        workflow_id=command.workflow_id,
        run_id=command.run_id,
    )
    return payload


def start_intent_payload(
    start_command: dict[str, Any],
    *,
    request_id: str,
) -> dict[str, Any]:
    payload = {
        "schema": WORKFLOW_CONTROL_DISPATCH_PAYLOAD_SCHEMA,
        "request_id": _identity(request_id, "start_request_id"),
        "start": dict(start_command),
    }
    _validate_payload(
        payload,
        kind=DISPATCH_KIND_START,
        tenant_id=str(start_command.get("tenant_id") or ""),
        workflow_id=str(start_command.get("workflow_id") or ""),
        run_id=str(start_command.get("run_id") or ""),
    )
    return payload


def _validate_payload(
    payload: Any,
    *,
    kind: str,
    tenant_id: str,
    workflow_id: str,
    run_id: str,
) -> None:
    if not isinstance(payload, dict) or payload.get("schema") != WORKFLOW_CONTROL_DISPATCH_PAYLOAD_SCHEMA:
        raise WorkflowControlDispatchIntentError("workflow_control_dispatch_payload_invalid")
    expected_keys = {"schema", "command"} if kind == DISPATCH_KIND_COMMAND else {"schema", "request_id", "start"}
    if set(payload) != expected_keys:
        raise WorkflowControlDispatchIntentError("workflow_control_dispatch_payload_shape_invalid")
    if kind == DISPATCH_KIND_COMMAND:
        raw = payload.get("command")
        if not isinstance(raw, dict):
            raise WorkflowControlDispatchIntentError("workflow_control_dispatch_command_invalid")
        command = SignedWorkflowCommand.from_mapping(raw)
        if command.tenant_id != tenant_id or command.workflow_id != workflow_id or command.run_id != run_id:
            raise WorkflowControlDispatchIntentError("workflow_control_dispatch_binding_mismatch")
        maximum = _MAX_COMMAND_PAYLOAD_BYTES
    else:
        _identity(payload.get("request_id"), "start_request_id")
        raw = payload.get("start")
        if not isinstance(raw, dict) or set(raw) != {
            "schema",
            "tenant_id",
            "workflow_id",
            "run_id",
            "workflow_request",
        }:
            raise WorkflowControlDispatchIntentError("workflow_control_dispatch_start_invalid")
        if raw.get("schema") != DURABLE_RUN_START_SCHEMA:
            raise WorkflowControlDispatchIntentError("workflow_control_dispatch_start_schema_invalid")
        request_raw = raw.get("workflow_request")
        if not isinstance(request_raw, dict):
            raise WorkflowControlDispatchIntentError("workflow_control_dispatch_start_invalid")
        request = WorkflowRequest.from_mapping(request_raw)
        if (
            raw.get("tenant_id") != tenant_id
            or raw.get("workflow_id") != workflow_id
            or raw.get("run_id") != run_id
            or request.workflow_id != workflow_id
            or request.metadata.get("tenant_id") != tenant_id
            or request.metadata.get("run_id") != run_id
        ):
            raise WorkflowControlDispatchIntentError("workflow_control_dispatch_binding_mismatch")
        maximum = _MAX_START_PAYLOAD_BYTES
    try:
        size = len(canonical_json(payload).encode("utf-8"))
    except (TypeError, ValueError) as exc:
        raise WorkflowControlDispatchIntentError("workflow_control_dispatch_payload_invalid") from exc
    if size > maximum:
        raise WorkflowControlDispatchIntentError("workflow_control_dispatch_payload_too_large")


def _identity(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not _IDENTITY_RE.fullmatch(value):
        raise WorkflowControlDispatchIntentError(f"workflow_control_dispatch_{field_name}_invalid")
    return value


def _bounded_text(value: Any, maximum: int, field_name: str, *, empty: bool) -> str:
    if (
        not isinstance(value, str)
        or value != value.strip()
        or len(value) > maximum
        or (not empty and not value)
        or any(not character.isprintable() or character in {"\x00", "\x7f"} for character in value)
    ):
        raise WorkflowControlDispatchIntentError(f"workflow_control_dispatch_{field_name}_invalid")
    return value


__all__ = [
    "DISPATCH_KIND_COMMAND",
    "DISPATCH_KIND_START",
    "DISPATCH_STATE_COMPLETED",
    "DISPATCH_STATE_DISPATCHING",
    "DISPATCH_STATE_OBSERVATION_PENDING",
    "DISPATCH_STATE_READY",
    "DISPATCH_STATE_REJECTED",
    "WORKFLOW_CONTROL_DISPATCH_PAYLOAD_SCHEMA",
    "WorkflowControlDispatchIntent",
    "WorkflowControlDispatchIntentError",
    "WorkflowControlDispatchIntentStore",
    "command_intent_payload",
    "start_intent_payload",
]
