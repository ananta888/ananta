"""Shared guards and small policies of the task-scoped propose/execute steps.

Split out of ``_task_scoped_step_orchestrator`` (SRP): the recovery-outcome
cache and vector-index policy aliases, the organization-research Hub guard,
the handler-only / knowledge-index handler availability responses, the
dispatch-admission error envelope and the request run-evidence context.
``_task_scoped_step_orchestrator`` re-exports every name.
"""

from __future__ import annotations

from typing import Any

from agent.services import _task_scoped_recovery_outcome_cache, _task_scoped_vector_step_policy

_RECOVERY_OUTCOME_CACHE = _task_scoped_recovery_outcome_cache.RECOVERY_OUTCOME_CACHE
_RECOVERY_OUTCOME_CACHE_LOCK = _task_scoped_recovery_outcome_cache.RECOVERY_OUTCOME_CACHE_LOCK
_cache_recovery_outcome = _task_scoped_recovery_outcome_cache.cache_recovery_outcome
_cached_recovery_outcome = _task_scoped_recovery_outcome_cache.cached_recovery_outcome
_recovery_cache_key = _task_scoped_recovery_outcome_cache.recovery_cache_key
_vector_index_domain_binding_error = _task_scoped_vector_step_policy.vector_index_domain_binding_error
_vector_index_handler_unavailable = _task_scoped_vector_step_policy.vector_index_handler_unavailable
_INTERACTIVE_TERMINAL_FINALIZE_COMMAND = "__ANANTA_FINALIZE_INTERACTIVE_OPENCODE__"


def _organization_research_hub_execution_guard(
    *,
    task: dict[str, Any],
    tid: str,
    phase: str,
):
    """Keep authoritative Organization research on its secure dispatch path."""

    from agent.config import settings
    from agent.services.organization_task_dispatch_gate_service import (
        organization_research_requires_secure_delegation,
    )
    from agent.services.task_scoped_execution_service import (
        TaskScopedRouteResponse,
    )

    if (
        str(settings.role or "").strip().lower() != "hub"
        or not organization_research_requires_secure_delegation(task)
    ):
        return None
    reason_code = "organization_research_secure_delegation_required"
    return TaskScopedRouteResponse(
        data={
            "status": "denied",
            "reason_code": reason_code,
            "task_id": tid,
            "phase": phase,
        },
        status="denied",
        message=reason_code,
        code=409,
    )


def _knowledge_index_handler_unavailable(
    *,
    task: dict[str, Any],
    phase: str,
):
    from agent.services.task_scoped_execution_service import (
        TaskScopedRouteResponse,
    )

    return TaskScopedRouteResponse(
        data={
            "status": "unavailable",
            "reason_code": "knowledge_index_worker_handler_unavailable",
            "task_id": str(task.get("id") or ""),
            "task_kind": "codecompass_index_build",
            "phase": str(phase),
        },
        status="error",
        message="Knowledge index worker handler unavailable",
        code=503,
    )


# Task kinds only a registered Worker handler may propose/execute (never an LLM).
HANDLER_ONLY_TASK_KINDS = frozenset({"codecompass_layer_build"})


def _handler_only_unavailable(*, task: dict, task_kind: str, phase: str):
    from agent.services.task_scoped_execution_service import (
        TaskScopedRouteResponse,
    )

    return TaskScopedRouteResponse(
        data={
            "status": "unavailable",
            "reason_code": "worker_handler_unavailable",
            "task_id": str(task.get("id") or ""),
            "task_kind": task_kind,
            "phase": str(phase),
        },
        status="error",
        message="Worker handler unavailable",
        code=503,
    )


def _dispatch_admission_error(*, tid: str, phase: str, decision):
    from agent.services.task_scoped_execution_service import (
        TaskScopedRouteResponse,
    )

    return TaskScopedRouteResponse(
        data={
            "status": "skipped",
            "reason": decision.reason_code,
            "task_id": tid,
            "phase": phase,
            "source_task_id": decision.source_task_id,
            "plan_id": decision.plan_id,
            "release_epoch": decision.release_epoch,
        },
        status="skipped",
        message="Recovery dispatch admission denied",
        code=409,
    )


def _apply_request_run_evidence_context(
    *,
    task: dict[str, Any],
    request_data: Any,
) -> None:
    """Apply a lease-validated context only to this request-local Task."""

    value = getattr(
        request_data,
        "recovery_run_evidence_context",
        None,
    )
    if value is None:
        return
    details = dict(task.get("status_reason_details") or {})
    details["recovery_tool_run_context"] = dict(value)
    task["status_reason_details"] = details
