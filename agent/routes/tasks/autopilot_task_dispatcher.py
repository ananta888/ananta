"""Autopilot dispatch of one task to a worker (hub-owned orchestration).

:func:`_dispatch_one_task_inner` orchestrates the dispatch phases, which live in
single-responsibility siblings:

* :mod:`.autopilot_dispatch_context` - the per-dispatch parameter object.
* :mod:`.autopilot_dispatch_preflight` - gates, hand-off/throttling and
  execution-scope allocation.
* :mod:`.autopilot_proposal_strategies` - propose guards and the strategy loop.
* :mod:`.autopilot_strategy_exhaustion` - model recovery and retry scheduling
  after all strategies failed.
* :mod:`.autopilot_dispatch_dependencies` - the collaborators every phase
  delegates to, and their per-application override seam.

This module keeps the long-context helpers, the propose-failure handling and the
evaluation/execution of an accepted proposal. The coordinator resolves the
:class:`.autopilot_dispatch_dependencies.DispatchDependencies` bundle once per
dispatch (or receives it explicitly) and passes it to every phase inside the
:class:`.autopilot_dispatch_context.DispatchContext`.
"""

from __future__ import annotations

import time
from dataclasses import replace
from typing import Any, Callable

from .autopilot_dispatch_context import DispatchContext
from .autopilot_dispatch_dependencies import (
    AUTOPILOT_DISPATCH_DEPENDENCIES,
    DispatchDependencies,
)
from .autopilot_dispatch_preflight import (
    allocate_execution_scope,
    evaluate_dispatch_gates,
    hand_off_assigned_task,
    throttle_repeated_propose,
)
from .autopilot_proposal_strategies import run_proposal_strategies
from .autopilot_strategy_exhaustion import handle_strategy_exhaustion
from .autopilot_task_dispatcher_helpers import (
    TaskDispatchResult,
    _effective_agent_cfg_for_task,
    _ensure_llm_profile_snapshot,
    _execute_proposed_step,
    _is_terminal_status,
    _is_transient_worker_transport_error,
    _merged_last_proposal_snapshot,
    _resolve_non_executable_terminal_status,
    _task_log,
)


def _hub_context_window(loop: Any) -> dict[str, Any]:
    """The window the Hub sizes tasks for, handed to the worker with every propose/execute (hub-owned)."""
    from agent.context_profile import hub_assignment

    try:
        return hub_assignment(loop._agent_config() or {})
    except Exception:  # noqa: BLE001 -- without it the worker uses its own configuration
        return {}

def _split_if_beyond_context(task: Any, *, app: Any) -> Any:
    """LCTX-007/008: split a task whose context exceeds the window (only with context_strategy.mode=active)."""
    try:
        config = ((getattr(app, "config", None) or {}).get("AGENT_CONFIG", {}) or {}).get("context_strategy")
        if str((config or {}).get("mode") or "shadow").strip().lower() != "active":
            return None
        from agent.services.long_context_coordinator import get_long_context_coordinator
        from agent.services.task_execution_window_service import execution_window

        # the window of the runtime that will execute the task: a subscription/cloud agent keeps its own
        # (e.g. claude-cli 200k), only local runtimes are sized for the Ananta profile
        window = execution_window(task, (getattr(app, "config", None) or {}).get("AGENT_CONFIG", {}) or {})
        return get_long_context_coordinator().maybe_split(task, config=config, window_tokens=window.tokens)
    except Exception:  # noqa: BLE001 -- a failed split leaves the normal dispatch path
        import logging

        logging.getLogger(__name__).warning("long-context split failed for %s", getattr(task, "id", "?"),
                                            exc_info=True)
        return None


_OVERFLOW_MARKERS = ("context length", "context window", "context_length", "too many tokens", "n_ctx",
                     "token_budget_exceeded", "context_too_large", "context_overflow", "maximum context")


def _is_context_overflow(strategy_failures: list[dict[str, Any]]) -> bool:
    """Did any attempt fail because the task did not fit the model window?"""
    for failure in strategy_failures or []:
        if str(failure.get("failure_type") or "") == "preflight_context_limit":
            return True
        text = " ".join(str(failure.get(key) or "") for key in ("reason", "error", "failure_type")).lower()
        if any(marker in text for marker in _OVERFLOW_MARKERS):
            return True
    return False


def _handle_context_overflow(task: Any, *, app: Any) -> Any:
    try:
        config = ((getattr(app, "config", None) or {}).get("AGENT_CONFIG", {}) or {}).get("context_strategy")
        if str((config or {}).get("mode") or "shadow").strip().lower() != "active":
            return None
        from agent.services.long_context_coordinator import get_long_context_coordinator

        return get_long_context_coordinator().maybe_split(task, config=config, overflowed=True)
    except Exception:  # noqa: BLE001 -- falls back to the normal exhaustion handling
        import logging

        logging.getLogger(__name__).warning("context overflow handling failed for %s", getattr(task, "id", "?"),
                                            exc_info=True)
        return None


def _dispatch_one_task_inner(
    *,
    task: Any,
    target_worker: Any,
    was_assigned: bool,
    loop: Any,
    services: Any,
    policy: dict,
    fallback_policy: dict,
    runtime_caps: dict,
    queue_positions: dict,
    local_worker_url: str,
    append_trace_event: Callable[..., None],
    update_local_task_status: Callable[..., None],
    dependencies: DispatchDependencies | None = None,
) -> TaskDispatchResult:
    if dependencies is None:
        dependencies = AUTOPILOT_DISPATCH_DEPENDENCIES.resolve()
    log = _task_log(task.id)  # thr-009
    result = TaskDispatchResult(task_id=task.id)
    # Skip stale dispatch candidates when a parallel thread already finalized
    # this task in the database.
    app_ctx = getattr(loop, "_app", None)
    recovery_gate = dependencies.recovery_dispatch_gate()
    ctx = DispatchContext(
        task=task,
        target_worker=target_worker,
        loop=loop,
        services=services,
        app_ctx=app_ctx,
        result=result,
        recovery_gate=recovery_gate,
        append_trace_event=append_trace_event,
        update_local_task_status=update_local_task_status,
        dependencies=dependencies,
        log=log,
    )
    stop_result, current_task = evaluate_dispatch_gates(ctx)
    if stop_result is not None:
        return stop_result

    split = _split_if_beyond_context(current_task or task, app=app_ctx)
    if split is not None:
        append_trace_event(task.id, "autopilot_long_context_" + ("externalized" if split.dispatch_now else "handled"),
                           strategy=split.strategy, steps=len(split.step_ids), final_step_id=split.final_step_id)
        if not split.dispatch_now:
            # LCTX: split into Hub step tasks (it waits for them) or paused for a decision -- not a failure
            result.dispatched = True
            return result
        # externalized: the task now fits and goes on to a worker with its material as a workspace file
        task = dependencies.repository_registry(app_ctx).task_repo.get_by_id(task.id) or task
        current_task = task
        ctx = replace(ctx, task=task)

    stop_result = hand_off_assigned_task(ctx) if was_assigned else throttle_repeated_propose(ctx)
    if stop_result is not None:
        return stop_result
    stop_result = allocate_execution_scope(
        ctx,
        fallback_policy=fallback_policy,
        queue_positions=queue_positions,
        local_worker_url=local_worker_url,
    )
    if stop_result is not None:
        return stop_result

    model_meta: dict[str, Any] = {}
    strategy_state = dependencies.extract_strategy_state(task)
    try:
        selected_model, model_meta = dependencies.select_model_for_task(
            loop=loop,
            task=task,
            excluded_models=set(strategy_state.get("failed_models") or []),
        )
        model_meta["selected_model"] = selected_model
        strategy_candidates = dependencies.proposal_strategy_candidates(
            loop=loop,
            task=task,
            base_model_meta=model_meta,
            state=strategy_state,
        )
        outcome = run_proposal_strategies(
            ctx,
            strategy_candidates=strategy_candidates,
            runtime_caps=runtime_caps,
            context_window=_hub_context_window,
        )
        if outcome.stop_result is not None:
            return outcome.stop_result
        propose_data = outcome.propose_data
        strategy_failures = outcome.strategy_failures

        if propose_data is None and _is_context_overflow(strategy_failures):
            handled = _handle_context_overflow(current_task or task, app=app_ctx)
            if handled is not None:
                # LCTX-009: the attempts ran out of window -- compact / externalize / split and try again
                append_trace_event(task.id, "autopilot_context_overflow_handled", strategy=handled.strategy,
                                   steps=len(handled.step_ids))
                result.dispatched = True
                return result
        if propose_data is None:
            return handle_strategy_exhaustion(
                ctx,
                outcome=outcome,
                strategy_state=strategy_state,
                model_meta=model_meta,
                runtime_caps=runtime_caps,
            )

        model_meta.update(outcome.selected_attempt_meta)
    except Exception as e:
        return _handle_propose_failure(ctx, e)

    return _evaluate_and_execute_proposal(
        ctx,
        propose_data=propose_data,
        model_meta=model_meta,
        policy=policy,
    )


def _handle_propose_failure(ctx: DispatchContext, exc: Exception) -> TaskDispatchResult:
    """Defer on transient worker transport errors, otherwise fail the task and release its workspace."""
    task = ctx.task
    target_worker = ctx.target_worker
    loop = ctx.loop
    app_ctx = ctx.app_ctx
    result = ctx.result
    append_trace_event = ctx.append_trace_event
    update_local_task_status = ctx.update_local_task_status
    current_task_status = ctx.dependencies.current_task_status
    if _is_transient_worker_transport_error(exc):
        defer_until = time.time() + 30
        update_local_task_status(
            task.id,
            "todo",
            manual_override_until=defer_until,
            error=f"transient_worker_transport_error:{str(exc)[:180]}",
        )
        append_trace_event(
            task.id,
            "autopilot_worker_transport_deferred",
            delegated_to=target_worker.url,
            reason=str(exc),
            defer_seconds=30,
        )
        result.failed = True
        result.failure_type = "propose_transport_deferred"
        return result
    latest_status = current_task_status(task.id, app=app_ctx)
    if _is_terminal_status(latest_status):
        append_trace_event(
            task.id,
            "autopilot_worker_failed_skipped_terminal",
            delegated_to=target_worker.url,
            terminal_status=latest_status,
            reason=str(exc),
        )
        result.dispatched = True
        result.completed = latest_status == "completed"
        result.failed = latest_status != "completed"
        result.failure_type = None if result.completed else latest_status
        return result
    update_local_task_status(task.id, "failed", error=str(exc))
    append_trace_event(task.id, "autopilot_worker_failed", delegated_to=target_worker.url, reason=str(exc))
    append_trace_event(
        task.id,
        "workspace_released",
        delegated_to=target_worker.url,
        workspace_id=f"ws-{task.id}",
        lease_id=f"lease-{task.id}",
        cleanup_state="failed",
    )
    open_until, failure_streak = loop._circuit_open_details(target_worker.url)
    if loop._is_worker_circuit_open(target_worker.url):
        append_trace_event(
            task.id,
            "autopilot_worker_circuit_open",
            worker_url=target_worker.url,
            reason="forward_failed",
            open_until=open_until,
            failure_streak=failure_streak,
        )
    result.failed = True
    result.failure_type = "propose_exception"
    return result


def _evaluate_and_execute_proposal(
    ctx: DispatchContext,
    *,
    propose_data: dict[str, Any],
    model_meta: dict[str, Any],
    policy: dict,
) -> TaskDispatchResult:
    """Snapshot the accepted proposal, enforce tool guardrails and execute the step."""
    task = ctx.task
    target_worker = ctx.target_worker
    loop = ctx.loop
    services = ctx.services
    app_ctx = ctx.app_ctx
    result = ctx.result
    append_trace_event = ctx.append_trace_event
    update_local_task_status = ctx.update_local_task_status
    current_task_status = ctx.dependencies.current_task_status
    log = ctx.log
    command = propose_data.get("command")
    tool_calls = propose_data.get("tool_calls")
    reason = propose_data.get("reason")
    proposal_snapshot = services.autopilot_decision_service.build_proposal_snapshot(propose_data)
    strategy_id = (
        ((proposal_snapshot.get("routing") or {}).get("propose_strategy_meta") or {}).get("selected_strategy")
        if isinstance(proposal_snapshot.get("routing"), dict)
        else None
    )
    proposal_snapshot = _ensure_llm_profile_snapshot(
        snapshot=proposal_snapshot,
        strategy_id=strategy_id,
        model_meta=model_meta if isinstance(model_meta, dict) else None,
        allow_synthetic_fallback=bool(
            ((loop._agent_config() or {}).get("llm_profile_policy") or {}).get("allow_synthetic_fallback", False)
        ),
    )
    if isinstance(model_meta, dict):
        proposal_snapshot["model_selection"] = dict(model_meta)
    raw_preview = proposal_snapshot.get("raw_preview")

    if not command and not tool_calls:
        latest_status = current_task_status(task.id, app=app_ctx)
        if _is_terminal_status(latest_status):
            append_trace_event(
                task.id,
                "autopilot_no_executable_step_skipped_terminal",
                delegated_to=target_worker.url,
                terminal_status=latest_status,
            )
            result.dispatched = True
            result.completed = latest_status == "completed"
            result.failed = latest_status != "completed"
            result.failure_type = None if result.completed else latest_status
            return result
        terminal_status = _resolve_non_executable_terminal_status(
            agent_cfg=_effective_agent_cfg_for_task(loop=loop, task=task)
        )
        update_local_task_status(
            task.id,
            terminal_status,
            error="autopilot_no_executable_step",
            last_proposal=_merged_last_proposal_snapshot(task_id=task.id, snapshot=proposal_snapshot, app=app_ctx),
            force=True,
        )
        append_trace_event(
            task.id,
            "autopilot_decision_failed",
            delegated_to=target_worker.url,
            reason=reason or "autopilot_no_executable_step",
            raw_preview=raw_preview,
            backend=proposal_snapshot.get("backend"),
            routing_reason=((proposal_snapshot.get("routing") or {}).get("reason")),
        )
        result.failed = True
        result.failure_type = "no_executable_step"
        return result

    append_trace_event(
        task.id,
        "autopilot_decision",
        delegated_to=target_worker.url,
        reason=reason,
        command=command,
        tool_calls=tool_calls,
        model_override=(model_meta.get("selected_model") if isinstance(model_meta, dict) else None),
        temperature_override=(model_meta.get("selected_temperature") if isinstance(model_meta, dict) else None),
        model_override_source=(model_meta.get("source") if isinstance(model_meta, dict) else None),
        backend=proposal_snapshot.get("backend"),
        routing_reason=((proposal_snapshot.get("routing") or {}).get("reason")),
    )

    if tool_calls:
        decision = services.autopilot_decision_service.evaluate_tool_guardrails_for_autopilot(
            task=task,
            policy=policy,
            agent_cfg=loop._agent_config(),
            reason=reason,
            command=command,
            tool_calls=tool_calls,
        )
        if not decision.allowed:
            update_local_task_status(
                task.id,
                "failed",
                error=f"security_policy_tool_guardrail_blocked:{','.join(decision.reasons)}",
                last_proposal=_merged_last_proposal_snapshot(task_id=task.id, snapshot=proposal_snapshot, app=app_ctx),
            )
            append_trace_event(
                task.id,
                "autopilot_security_policy_blocked",
                delegated_to=target_worker.url,
                security_level=policy["level"],
                blocked_reasons=decision.reasons,
                blocked_tools=decision.blocked_tools,
                backend=proposal_snapshot.get("backend"),
                routing_reason=((proposal_snapshot.get("routing") or {}).get("reason")),
            )
            result.failed = True
            result.failure_type = "security_policy_blocked"
            return result

    return _execute_proposed_step(
        task=task,
        command=command,
        tool_calls=tool_calls,
        policy=policy,
        loop=loop,
        target_worker=target_worker,
        result=result,
        append_trace_event=append_trace_event,
        update_local_task_status=update_local_task_status,
        services=services,
        proposal_snapshot=proposal_snapshot,
        model_meta=model_meta,
        app_ctx=app_ctx,
        log=log,
    )
