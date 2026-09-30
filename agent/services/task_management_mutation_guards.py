"""Mutation guards of Hub task management.

Split out of ``task_management_service`` (SRP): the reusable guards every
task-management mutation runs before touching task state -- vector-index
admin authorization, Recovery mutation conflicts, the critical status
mutation gate (approval, execution risk, mutation gate, audit) -- and the
instruction-selection payload projection.  ``TaskManagementService`` keeps
its private method names as thin delegators.
"""

from __future__ import annotations

import time
from typing import Any, Callable

from flask import current_app

from agent.services.approval_policy_service import get_approval_policy_service
from agent.services.execution_audit_service import get_execution_audit_service
from agent.services.execution_risk_policy_service import evaluate_execution_risk
from agent.services.mutation_gate_service import get_mutation_gate_service
from agent.services.recovery_task_mutation_policy import (
    RecoveryTaskMutationConflict,
    ensure_external_recovery_mutation_allowed,
)
from agent.services.vector_store_authorization_policy import (
    VectorAdminAuthorizationContext,
    get_vector_store_authorization_policy,
    has_reserved_vector_index_marker,
)
from agent.services.vector_task_admin_guard_service import (
    require_authoritative_vector_task,
)


def vector_admin_error(
    task: Any,
    *,
    authorization: (VectorAdminAuthorizationContext | None),
) -> dict[str, Any] | None:
    if not has_reserved_vector_index_marker(task):
        return None
    try:
        get_vector_store_authorization_policy().require_task_admin(
            authorization,
            task,
        )
    except PermissionError as exc:
        reason = str(exc)
        return {
            "error": reason,
            "code": 403,
            "data": {"reason_code": reason},
        }

    try:
        require_authoritative_vector_task(task)
    except ValueError as exc:
        reason = str(exc)
        return {
            "error": reason,
            "code": 409,
            "data": {"reason_code": reason},
        }
    return None


def recovery_mutation_conflict(
    task: Any,
    *,
    action: str,
) -> dict[str, Any] | None:
    try:
        ensure_external_recovery_mutation_allowed(
            task,
            action=action,
        )
    except RecoveryTaskMutationConflict as exc:
        return {
            "error": exc.reason_code,
            "code": 409,
            "data": exc.as_data(),
        }
    return None


def critical_state_mutation(status: str | None) -> bool:
    return str(status or "").strip().lower() in {"completed", "failed", "blocked", "cancelled"}


def enforce_task_state_mutation_gate(
    *,
    task_id: str,
    requested_status: str | None,
    task: dict | None,
    actor: Callable[[], str],
) -> tuple[bool, str | None]:
    """Run a critical status change through approval, risk and the mutation gate; audit the decision."""
    if not critical_state_mutation(requested_status):
        return True, None
    cfg = current_app.config.get("AGENT_CONFIG", {}) or {}
    tool_calls = [
        {
            "name": "task_state_update",
            "args": {"task_id": task_id, "to_status": str(requested_status or "").strip().lower()},
        }
    ]
    approval = get_approval_policy_service().evaluate(
        command=None,
        tool_calls=tool_calls,
        task=dict(task or {}),
        agent_cfg=cfg,
    )
    risk = evaluate_execution_risk(
        command=None,
        tool_calls=tool_calls,
        task=dict(task or {}),
        agent_cfg=cfg,
    )
    decision = (
        get_mutation_gate_service()
        .evaluate(
            command=None,
            tool_calls=tool_calls,
            task=dict(task or {}),
            agent_cfg=cfg,
            approval_decision=approval,
            risk_decision=risk,
            trace_id=str((task or {}).get("goal_trace_id") or "").strip() or None,
            actor=actor(),
        )
        .as_dict()
    )
    get_execution_audit_service().emit(
        operation_type="mutation_gate_decision",
        outcome=str(decision.get("classification") or "unknown"),
        trace_id=str((task or {}).get("goal_trace_id") or "").strip() or None,
        goal_id=(task or {}).get("goal_id"),
        task_id=task_id,
        actor_role="hub",
        details={
            "reason_code": decision.get("reason_code"),
            "mutation_class": decision.get("mutation_class"),
            "normalized_target": decision.get("normalized_target"),
            "approval_scope": decision.get("approval_scope"),
            "source": "task_management_service",
            "requested_status": str(requested_status or "").strip().lower(),
        },
    )
    if decision.get("classification") in {"blocked", "confirm_required"}:
        return False, str(decision.get("reason_code") or "mutation_gate_blocked")
    return True, None


def apply_instruction_selection_to_payload(
    payload: dict[str, Any],
    *,
    default_owner: str | None = None,
) -> None:
    owner_username = (
        str(
            payload.pop(
                "instruction_owner_username",
                "",
            )
            or ""
        ).strip()
        or default_owner
    )
    profile_id = str(payload.pop("instruction_profile_id", "") or "").strip() or None
    overlay_id = str(payload.pop("instruction_overlay_id", "") or "").strip() or None
    if not owner_username and not profile_id and not overlay_id:
        return
    worker_execution_context = dict(payload.get("worker_execution_context") or {})
    instruction_context = dict(worker_execution_context.get("instruction_context") or {})
    if owner_username:
        instruction_context["owner_username"] = owner_username
    instruction_context["profile_id"] = profile_id
    instruction_context["overlay_id"] = overlay_id
    instruction_context["updated_at"] = time.time()
    worker_execution_context["instruction_context"] = instruction_context
    payload["worker_execution_context"] = worker_execution_context


__all__ = [
    "apply_instruction_selection_to_payload",
    "critical_state_mutation",
    "enforce_task_state_mutation_gate",
    "recovery_mutation_conflict",
    "vector_admin_error",
]
