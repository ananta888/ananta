"""Autopilot dispatch preflight: gates, hand-off/throttling and execution-scope allocation.

Each phase returns a :class:`TaskDispatchResult` when dispatch must stop, or
``None`` to continue. Behavior is identical to the former inline blocks of
:func:`agent.routes.tasks.autopilot_task_dispatcher._dispatch_one_task_inner`.
"""

from __future__ import annotations

import time
from typing import Any

from agent.config import settings
from agent.services.organization_task_dispatch_gate_service import (
    get_organization_task_dispatch_gate_service,
)

from .autopilot_dispatch_context import DispatchContext
from .autopilot_task_dispatcher_helpers import (
    TaskDispatchResult,
    _is_terminal_status,
    _recent_strategy_attempts,
)


def evaluate_dispatch_gates(ctx: DispatchContext) -> tuple[TaskDispatchResult | None, Any]:
    """Recovery, organization-lifecycle, terminal-task and terminal-goal gates.

    Returns ``(stop_result, current_task)``; ``stop_result`` is ``None`` when
    dispatch may continue. ``current_task`` is the authoritative task row.
    """
    task = ctx.task
    target_worker = ctx.target_worker
    app_ctx = ctx.app_ctx
    result = ctx.result
    recovery_gate = ctx.recovery_gate
    append_trace_event = ctx.append_trace_event
    current_task_status = ctx.dependencies.current_task_status
    with recovery_gate.dispatch_guard(
        task.id,
        app=app_ctx,
    ) as gate_decision:
        if not gate_decision.allowed:
            append_trace_event(
                task.id,
                "autopilot_dispatch_skipped_recovery_gate",
                delegated_to=target_worker.url,
                reason_code=gate_decision.reason_code,
                plan_id=gate_decision.plan_id,
                source_task_id=gate_decision.source_task_id,
                release_epoch=gate_decision.release_epoch,
            )
            result.dispatched = True
            result.failed = True
            result.failure_type = gate_decision.reason_code
            return result, None
    current_task = ctx.dependencies.repository_registry(app_ctx).task_repo.get_by_id(task.id)
    organization_decision = (
        get_organization_task_dispatch_gate_service().evaluate(
            current_task or task
        )
    )
    if not organization_decision.allowed:
        append_trace_event(
            task.id,
            "autopilot_dispatch_skipped_organization_lifecycle",
            delegated_to=target_worker.url,
            reason_code=organization_decision.reason_code,
        )
        result.failure_type = organization_decision.reason_code
        return result, current_task
    latest_status = current_task_status(task.id, app=app_ctx)
    if _is_terminal_status(latest_status):
        append_trace_event(
            task.id,
            "autopilot_dispatch_skipped_terminal",
            delegated_to=target_worker.url,
            terminal_status=latest_status,
        )
        result.dispatched = True
        result.completed = latest_status == "completed"
        result.failed = latest_status != "completed"
        result.failure_type = None if result.completed else latest_status
        return result, current_task

    # Skip dispatch if the parent goal is already in a terminal state.
    goal_id = str(getattr(task, "goal_id", "") or "").strip()
    if goal_id:
        repos = ctx.dependencies.repository_registry(app_ctx)
        goal_obj = repos.goal_repo.get_by_id(goal_id)
        goal_status = str(getattr(goal_obj, "status", "") or "").strip().lower()
        if goal_status in {
            "completed",
            "failed",
            "cancelled",
            "aborted",
            "timeout",
            "archived",
        }:
            append_trace_event(
                task.id,
                "autopilot_dispatch_skipped_goal_terminal",
                delegated_to=target_worker.url,
                goal_status=goal_status,
            )
            result.dispatched = True
            result.failed = True
            result.failure_type = f"goal_{goal_status}"
            return result, current_task
    return None, current_task


def hand_off_assigned_task(ctx: DispatchContext) -> TaskDispatchResult | None:
    """Assign a freshly selected task to its worker unless review- or recovery-gated."""
    task = ctx.task
    target_worker = ctx.target_worker
    app_ctx = ctx.app_ctx
    result = ctx.result
    recovery_gate = ctx.recovery_gate
    append_trace_event = ctx.append_trace_event
    update_local_task_status = ctx.update_local_task_status
    current_task_status = ctx.dependencies.current_task_status
    latest_status = current_task_status(task.id, app=app_ctx)
    if latest_status in {"waiting_for_review", "needs_review"}:
        append_trace_event(
            task.id,
            "autopilot_handoff_skipped_review_gated",
            delegated_to=target_worker.url,
            status=latest_status,
        )
        # Review-gated tasks must not be re-assigned by autopilot.
        result.dispatched = True
        return result
    if _is_terminal_status(latest_status):
        append_trace_event(
            task.id,
            "autopilot_handoff_skipped_terminal",
            delegated_to=target_worker.url,
            terminal_status=latest_status,
        )
        result.dispatched = True
        result.completed = latest_status == "completed"
        result.failed = latest_status != "completed"
        result.failure_type = None if result.completed else latest_status
        return result
    with recovery_gate.dispatch_guard(
        task.id,
        app=app_ctx,
    ) as gate_decision:
        if not gate_decision.allowed:
            append_trace_event(
                task.id,
                "autopilot_assignment_skipped_recovery_gate",
                delegated_to=target_worker.url,
                reason_code=gate_decision.reason_code,
                plan_id=gate_decision.plan_id,
                source_task_id=gate_decision.source_task_id,
                release_epoch=gate_decision.release_epoch,
            )
            result.dispatched = True
            result.failed = True
            result.failure_type = gate_decision.reason_code
            return result
        update_local_task_status(
            task.id,
            "assigned",
            assigned_agent_url=target_worker.url,
            assigned_agent_token=target_worker.token,
        )
    append_trace_event(
        task.id,
        "autopilot_handoff",
        delegated_to=target_worker.url,
        reason="round_robin_assignment",
    )
    return None


def throttle_repeated_propose(ctx: DispatchContext) -> TaskDispatchResult | None:
    """Defer tasks whose propose attempts repeat too quickly while already assigned."""
    task = ctx.task
    target_worker = ctx.target_worker
    app_ctx = ctx.app_ctx
    result = ctx.result
    append_trace_event = ctx.append_trace_event
    update_local_task_status = ctx.update_local_task_status
    current_task_status = ctx.dependencies.current_task_status
    # Throttle repeated propose attempts for already-assigned tasks.
    # Without this guard, tight autopilot ticks can flood propose calls,
    # quickly tripping hard-guard windows without meaningful progress.
    current_status = current_task_status(task.id, app=app_ctx)
    if str(current_status or "").strip().lower() == "assigned":
        recent_attempts_short = _recent_strategy_attempts(
            task,
            now_ts=time.time(),
            window_seconds=20,
        )
        if recent_attempts_short >= 3:
            defer_until = time.time() + 20
            update_local_task_status(
                task.id,
                "assigned",
                manual_override_until=defer_until,
                event_type="autopilot_strategy_attempt_throttled",
                event_actor="autopilot_tick",
                force=True,
            )
            append_trace_event(
                task.id,
                "autopilot_strategy_attempt_throttled",
                delegated_to=target_worker.url,
                recent_attempts=recent_attempts_short,
                window_seconds=20,
                defer_seconds=20,
            )
            result.dispatched = True
            return result
    return None


def allocate_execution_scope(
    ctx: DispatchContext,
    *,
    fallback_policy: dict,
    queue_positions: dict,
    local_worker_url: str,
) -> TaskDispatchResult | None:
    """Enforce the hub-as-worker fallback policy, bind the workflow assignment and
    allocate the task-scoped workspace."""
    task = ctx.task
    target_worker = ctx.target_worker
    app_ctx = ctx.app_ctx
    result = ctx.result
    append_trace_event = ctx.append_trace_event
    update_local_task_status = ctx.update_local_task_status
    current_task_status = ctx.dependencies.current_task_status
    is_local_fallback = (
        settings.role == "hub"
        and settings.hub_can_be_worker
        and target_worker.url.rstrip("/") == local_worker_url
    )
    if is_local_fallback and not fallback_policy["allow_hub_worker_fallback"]:
        blocked_status = fallback_policy["fallback_block_status"]
        update_local_task_status(
            task.id,
            blocked_status,
            verification_status={
                **dict(getattr(task, "verification_status", None) or {}),
                "execution_provenance": {
                    "execution_mode": "fallback_blocked",
                    "fallback_reason": "hub_worker_fallback_disallowed",
                    "blocked_at": time.time(),
                },
            },
        )
        append_trace_event(
            task.id,
            "autopilot_fallback_blocked",
            delegated_to=target_worker.url,
            fallback_reason="hub_worker_fallback_disallowed",
            action="escalated" if fallback_policy["escalate_on_fallback_block"] else "blocked",
        )
        result.failed = True
        result.failure_type = "fallback_blocked"
        return result

    if is_local_fallback:
        append_trace_event(
            task.id,
            "hub_worker_fallback",
            delegated_to=target_worker.url,
            fallback_reason="no_remote_worker_selected",
            provenance={
                "mode": "hub_as_worker_fallback",
                "queue_position": queue_positions.get(task.id),
            },
        )
    try:
        from agent.services.workflow_worker_assignment_runtime import (
            bind_dispatched_workflow_task,
        )

        workflow_assignment = bind_dispatched_workflow_task(
            task=task,
            worker=target_worker,
        )
    except Exception as exc:  # fail closed before any Worker transport
        reason_code = str(
            getattr(
                exc,
                "reason_code",
                "workflow_worker_assignment_unavailable",
            )
        )
        update_local_task_status(
            task.id,
            "failed",
            error=reason_code,
            event_type="workflow_worker_assignment_failed",
            event_actor="hub_dispatcher",
            event_details={"reason_code": reason_code},
            force=True,
        )
        append_trace_event(
            task.id,
            "workflow_worker_assignment_failed",
            delegated_to=target_worker.url,
            reason_code=reason_code,
        )
        result.dispatched = True
        result.failed = True
        result.failure_type = reason_code
        return result
    if workflow_assignment is not None:
        append_trace_event(
            task.id,
            "workflow_worker_assignment_bound",
            delegated_to=workflow_assignment.worker_url,
            worker_id=workflow_assignment.worker_id,
            attempt_id=workflow_assignment.attempt_id,
            fencing_token=workflow_assignment.fencing_token,
        )
    append_trace_event(
        task.id,
        "execution_scope_allocated",
        delegated_to=target_worker.url,
        execution_scope={
            "executor_container": "hub" if target_worker.url.rstrip("/") == local_worker_url else "worker",
            "worker_url": target_worker.url,
            "queue_position": queue_positions.get(task.id),
        },
        workspace_id=f"ws-{task.id}",
        lease_id=f"lease-{task.id}",
        cleanup_state="pending",
    )
    current_status_for_scope = current_task_status(task.id, app=app_ctx)
    status_for_scope = current_status_for_scope or "assigned"
    update_local_task_status(
        task.id,
        status_for_scope,
        verification_status={
            **dict(getattr(task, "verification_status", None) or {}),
            "execution_scope": {
                "workspace_id": f"ws-{task.id}",
                "lease_id": f"lease-{task.id}",
                "lifecycle_status": "allocated",
                "isolation_mode": "task_scoped_workspace",
                "worker_url": target_worker.url,
                "execution_mode": "hub_as_worker_fallback" if is_local_fallback else "delegated_worker",
                "fallback_reason": "no_remote_worker_selected" if is_local_fallback else None,
            },
            "execution_provenance": {
                "execution_mode": "hub_as_worker_fallback" if is_local_fallback else "delegated_worker",
                "fallback_reason": "no_remote_worker_selected" if is_local_fallback else None,
                "updated_at": time.time(),
            },
        },
    )
    return None
