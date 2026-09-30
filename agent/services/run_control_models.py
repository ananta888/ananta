"""Run-Control value types, vocabularies and domain errors.

Pure dataclasses and constants shared by ``RunControlService`` and its
collaborators; serialization (``as_dict``) key order is part of the API.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from agent.services.identity_validation import require_canonical_identity


@dataclass(frozen=True)
class RunControlPrincipal:
    tenant_id: str
    subject_id: str

    @classmethod
    def from_values(cls, tenant_id: Any, subject_id: Any) -> "RunControlPrincipal":
        return cls(
            tenant_id=require_canonical_identity(tenant_id, field_name="tenant_id"),
            subject_id=require_canonical_identity(subject_id, field_name="subject_id"),
        )

COMMAND_TYPES = frozenset({
    "pause_run", "resume_run", "cancel_run", "retry_run_or_task",
    "inject_instruction", "select_branch", "approve_gate", "deny_gate",
})

INSTRUCTION_MODES = frozenset({
    "next_iteration_instruction", "pause_then_apply", "context_note_only",
})

INSTRUCTION_CLASSES = frozenset({
    "correction", "constraint", "preference", "branch_hint", "stop_condition",
})

BRANCH_TYPES = frozenset({
    "llm_comparison_variant", "planner_variant", "implementation_strategy",
    "repair_strategy", "security_hardened_variant",
})


@dataclass
class RunCommand:
    command_id: str
    type: str
    requested_by: str
    requested_at: float
    status: str  # accepted|rejected_by_policy|pending_safe_point|applied|superseded|failed
    task_id: str | None = None
    goal_id: str | None = None
    run_id: str | None = None
    payload: dict = field(default_factory=dict)
    result: dict = field(default_factory=dict)
    effective_at: float | None = None
    idempotency_key: str | None = None
    tenant_id: str = ""
    subject_id: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "command_id": self.command_id,
            "type": self.type,
            "task_id": self.task_id,
            "goal_id": self.goal_id,
            "run_id": self.run_id,
            "requested_by": self.requested_by,
            "requested_at": self.requested_at,
            "effective_at": self.effective_at,
            "status": self.status,
            "result": self.result,
            "idempotency_key": self.idempotency_key,
            "tenant_id": self.tenant_id,
            "subject_id": self.subject_id,
        }


@dataclass
class OperatorInstruction:
    instruction_id: str
    text: str
    actor: str
    created_at: float
    mode: str = "next_iteration_instruction"
    instruction_class: str = "constraint"
    status: str = "active"  # active|superseded|applied|resolved
    task_id: str | None = None
    goal_id: str | None = None
    run_id: str | None = None
    applied_at: float | None = None
    tenant_id: str = ""
    subject_id: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "instruction_id": self.instruction_id,
            "task_id": self.task_id,
            "goal_id": self.goal_id,
            "run_id": self.run_id,
            "mode": self.mode,
            "text": self.text,
            "instruction_class": self.instruction_class,
            "actor": self.actor,
            "created_at": self.created_at,
            "status": self.status,
            "applied_at": self.applied_at,
            "tenant_id": self.tenant_id,
            "subject_id": self.subject_id,
        }


@dataclass
class BranchCandidate:
    branch_id: str
    label: str
    branch_type: str = "llm_comparison_variant"
    status: str = "proposed"  # proposed|active|selected|paused|rejected|superseded|completed
    task_id: str | None = None
    goal_id: str | None = None
    description: str = ""
    metadata: dict = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    selected_at: float | None = None
    tenant_id: str = ""
    subject_id: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "branch_id": self.branch_id,
            "task_id": self.task_id,
            "goal_id": self.goal_id,
            "branch_type": self.branch_type,
            "label": self.label,
            "description": self.description,
            "status": self.status,
            "created_at": self.created_at,
            "selected_at": self.selected_at,
            "metadata": self.metadata,
            "tenant_id": self.tenant_id,
            "subject_id": self.subject_id,
        }


class RunCommandIdempotencyConflictError(RuntimeError):
    """Raised when an idempotency key is reused for a different request."""

    reason_code = "run_command_idempotency_conflict"

    def __init__(
        self,
        *,
        idempotency_key_ref: str,
        existing_command_id: str,
        mismatched_fields: tuple[str, ...],
    ) -> None:
        super().__init__(self.reason_code)
        self.idempotency_key_ref = idempotency_key_ref
        self.existing_command_id = existing_command_id
        self.mismatched_fields = mismatched_fields


class RunControlAuthorizationError(RuntimeError):
    reason_code = "run_control_resource_not_found"
