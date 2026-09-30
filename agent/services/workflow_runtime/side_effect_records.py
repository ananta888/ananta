"""Side-effect ledger record model and its fenced state machine.

Value types (``SideEffectRecord``/``SideEffectClaim``), the canonical event
mapping, and the pure transition function every ledger adapter delegates to,
so storage code cannot invent a second state machine.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from agent.services.workflow_runtime.errors import (
    FencingTokenError,
    InvalidTransitionError,
    OptimisticConcurrencyError,
)
from agent.services.workflow_runtime.events import CanonicalWorkflowEvent
from agent.services.workflow_runtime.side_effect_validation import (
    _nonnegative_integer,
    _positive_integer,
    _positive_timestamp,
)
from ananta_contracts.workflow_operation import operation_id_for

SIDE_EFFECT_LEDGER_SCHEMA = "ananta.side_effect_ledger.v1"
SIDE_EFFECT_CLASSES = frozenset({"read", "idempotent_write", "non_idempotent_write"})
SIDE_EFFECT_STATUSES = frozenset(
    {"planned", "authorized", "started", "completed", "failed", "uncertain", "compensated"}
)

_SIDE_EFFECT_RECORD_FIELDS = frozenset(
    {
        "schema",
        "operation_id",
        "tenant_id",
        "workflow_id",
        "run_id",
        "step_id",
        "declared_operation",
        "side_effect_class",
        "status",
        "revision",
        "fencing_token",
        "attempt_id",
        "authorization_envelope_id",
        "result_ref",
        "failure_code",
        "updated_at",
    }
)

_TRANSITIONS: dict[str, frozenset[str]] = {
    "planned": frozenset({"authorized", "failed"}),
    "authorized": frozenset({"started", "failed"}),
    "started": frozenset({"completed", "failed", "uncertain"}),
    "failed": frozenset({"authorized", "compensated"}),
    "uncertain": frozenset({"completed", "failed", "compensated"}),
    "completed": frozenset({"compensated"}),
    "compensated": frozenset(),
}


def side_effect_event(
    record: "SideEffectRecord",
    *,
    correlation_id: str,
    causation_id: str,
    actor: str = "hub",
) -> CanonicalWorkflowEvent:
    """Map a committed ledger revision to a deduplicable canonical event."""

    return CanonicalWorkflowEvent.build(
        tenant_id=record.tenant_id,
        workflow_id=record.workflow_id,
        run_id=record.run_id,
        step_id=record.step_id,
        event_type=f"workflow.side_effect.{record.status}",
        correlation_id=correlation_id,
        causation_id=causation_id,
        dedupe_key=f"side-effect:{record.operation_id}:{record.revision}",
        actor=actor,
        payload={
            "operation_id": record.operation_id,
            "declared_operation": record.declared_operation,
            "side_effect_class": record.side_effect_class,
            "fencing_token": record.fencing_token,
            "attempt_id": record.attempt_id,
            "result_ref": record.result_ref,
            "failure_code": record.failure_code,
        },
        occurred_at=record.updated_at,
        event_id=f"wfe-side-effect-{record.operation_id}-{record.revision}",
    )


@dataclass(frozen=True)
class SideEffectRecord:
    operation_id: str
    tenant_id: str
    workflow_id: str
    run_id: str
    step_id: str
    declared_operation: str
    side_effect_class: str
    status: str = "planned"
    revision: int = 1
    fencing_token: int = 0
    attempt_id: str = ""
    authorization_envelope_id: str = ""
    result_ref: str = ""
    failure_code: str = ""
    updated_at: float = 0.0
    schema: str = SIDE_EFFECT_LEDGER_SCHEMA

    def assert_valid(self) -> None:
        required = (
            self.operation_id,
            self.tenant_id,
            self.workflow_id,
            self.run_id,
            self.step_id,
            self.declared_operation,
            self.side_effect_class,
        )
        if any(not value for value in required):
            raise ValueError("side_effect_binding_required")
        expected_id = operation_id_for(
            tenant_id=self.tenant_id,
            run_id=self.run_id,
            step_id=self.step_id,
            declared_operation=self.declared_operation,
        )
        if self.operation_id != expected_id:
            raise ValueError("side_effect_operation_id_invalid")
        if self.status not in SIDE_EFFECT_STATUSES or self.revision < 1 or self.fencing_token < 0:
            raise ValueError("side_effect_state_invalid")
        if self.side_effect_class not in SIDE_EFFECT_CLASSES:
            raise ValueError("side_effect_class_invalid")
        if self.schema != SIDE_EFFECT_LEDGER_SCHEMA:
            raise ValueError("side_effect_schema_unsupported")

    @classmethod
    def from_mapping(cls, raw: dict[str, Any]) -> "SideEffectRecord":
        record = cls(
            operation_id=str(raw.get("operation_id") or ""),
            tenant_id=str(raw.get("tenant_id") or ""),
            workflow_id=str(raw.get("workflow_id") or ""),
            run_id=str(raw.get("run_id") or ""),
            step_id=str(raw.get("step_id") or ""),
            declared_operation=str(raw.get("declared_operation") or ""),
            side_effect_class=str(raw.get("side_effect_class") or ""),
            status=str(raw.get("status") or "planned"),
            revision=int(raw.get("revision") or 0),
            fencing_token=int(raw.get("fencing_token") or 0),
            attempt_id=str(raw.get("attempt_id") or ""),
            authorization_envelope_id=str(raw.get("authorization_envelope_id") or ""),
            result_ref=str(raw.get("result_ref") or ""),
            failure_code=str(raw.get("failure_code") or ""),
            updated_at=float(raw.get("updated_at") or 0),
            schema=str(raw.get("schema") or SIDE_EFFECT_LEDGER_SCHEMA),
        )
        record.assert_valid()
        return record

    @classmethod
    def from_exact_mapping(cls, raw: Mapping[str, object]) -> "SideEffectRecord":
        """Hydrate a transition-owned row without legacy coercion/defaults."""

        safe = _strict_side_effect_record_mapping(raw)
        record = cls(**safe)
        record.assert_valid()
        return record

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.schema,
            "operation_id": self.operation_id,
            "tenant_id": self.tenant_id,
            "workflow_id": self.workflow_id,
            "run_id": self.run_id,
            "step_id": self.step_id,
            "declared_operation": self.declared_operation,
            "side_effect_class": self.side_effect_class,
            "status": self.status,
            "revision": self.revision,
            "fencing_token": self.fencing_token,
            "attempt_id": self.attempt_id,
            "authorization_envelope_id": self.authorization_envelope_id,
            "result_ref": self.result_ref,
            "failure_code": self.failure_code,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class SideEffectClaim:
    record: SideEffectRecord
    acquired: bool
    reason: str


def _new_record(
    *,
    tenant_id: str,
    workflow_id: str,
    run_id: str,
    step_id: str,
    declared_operation: str,
    side_effect_class: str,
    timestamp: float | None = None,
) -> SideEffectRecord:
    record = SideEffectRecord(
        operation_id=operation_id_for(
            tenant_id=tenant_id,
            run_id=run_id,
            step_id=step_id,
            declared_operation=declared_operation,
        ),
        tenant_id=str(tenant_id).strip(),
        workflow_id=str(workflow_id).strip(),
        run_id=str(run_id).strip(),
        step_id=str(step_id).strip(),
        declared_operation=str(declared_operation).strip(),
        side_effect_class=str(side_effect_class).strip(),
        updated_at=float(time.time() if timestamp is None else timestamp),
    )
    record.assert_valid()
    return record


def _binding(record: SideEffectRecord) -> tuple[str, ...]:
    return (
        record.tenant_id,
        record.workflow_id,
        record.run_id,
        record.step_id,
        record.declared_operation,
        record.side_effect_class,
    )


def _transition(
    current: SideEffectRecord,
    *,
    expected_revision: int,
    fencing_token: int,
    to_status: str,
    attempt_id: str | object = "",
    authorization_envelope_id: str | object = "",
    result_ref: str | object = "",
    failure_code: str | object = "",
    require_exact_fence: bool = False,
) -> SideEffectRecord:
    if current.revision != int(expected_revision):
        raise OptimisticConcurrencyError(
            f"side_effect_revision_conflict:expected={expected_revision}:actual={current.revision}"
        )
    fence = int(fencing_token)
    if fence < current.fencing_token or (require_exact_fence and fence != current.fencing_token):
        raise FencingTokenError("side_effect_fencing_token_stale")
    if str(to_status) not in _TRANSITIONS.get(current.status, frozenset()):
        raise InvalidTransitionError(f"side_effect_transition_invalid:{current.status}:{to_status}")
    updated = replace(
        current,
        status=str(to_status),
        revision=current.revision + 1,
        fencing_token=fence,
        attempt_id=str(attempt_id or current.attempt_id),
        authorization_envelope_id=str(authorization_envelope_id or current.authorization_envelope_id),
        result_ref=str(result_ref or current.result_ref),
        failure_code=str(failure_code or ""),
        updated_at=time.time(),
    )
    updated.assert_valid()
    return updated


def _strict_side_effect_record_mapping(raw: Mapping[str, object]) -> dict[str, object]:
    if not isinstance(raw, Mapping) or set(raw) != _SIDE_EFFECT_RECORD_FIELDS:
        raise ValueError("workflow_transition_side_effect_ledger_record_invalid")
    safe = dict(raw)
    for name in (
        "schema",
        "operation_id",
        "tenant_id",
        "workflow_id",
        "run_id",
        "step_id",
        "declared_operation",
        "side_effect_class",
        "status",
        "attempt_id",
        "authorization_envelope_id",
        "result_ref",
        "failure_code",
    ):
        value = safe[name]
        if not isinstance(value, str) or "\x00" in value:
            raise ValueError("workflow_transition_side_effect_ledger_record_invalid")
        try:
            value.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ValueError("workflow_transition_side_effect_ledger_record_invalid") from exc
    _positive_integer(safe["revision"], "record_revision")
    _nonnegative_integer(safe["fencing_token"], "record_fencing_token")
    _positive_timestamp(safe["updated_at"], "record_updated_at")
    return safe


def _assert_side_effect_row_projection(
    record: SideEffectRecord,
    **values: object,
) -> None:
    if any(getattr(record, name) != value for name, value in values.items()):
        raise OptimisticConcurrencyError("workflow_transition_side_effect_ledger_projection_conflict")
