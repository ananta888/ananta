from __future__ import annotations

import concurrent.futures
import logging
import os
import time
from typing import Any, Callable

from agent.config import settings
from agent.metrics import DISPATCH_WAIT_SECONDS, TASK_QUEUE_WAIT_SECONDS
from agent.services.repository_registry import get_repository_registry
from agent.services.recovery_dispatch_gate_service import (
    get_recovery_dispatch_gate_service,
)
from agent.routes.tasks.autopilot_dispatch_policy import (
    build_tick_debug_payload,
    classify_no_candidate_reason,
    dispatch_queue_positions,
    resolve_dispatch_hard_timeout,
    resolve_effective_concurrency,
    resolve_target_worker_for_task,
)
from agent.routes.tasks.autopilot_stale_task_recovery import (
    StaleTaskRecovery,
    is_hub_managed_model_recovery_task,
    refresh_approval_lifecycle,
)
from agent.routes.tasks.autopilot_model_selector import (
    _normalize_model_candidate,
    _normalize_model_list,
    _normalize_override_map,
    _normalize_temperature_list,
    _normalize_temperature_value,
    _preferred_benchmark_provider,
    _select_model_for_task,
)
from agent.routes.tasks.autopilot_strategy_candidates import (
    _extract_strategy_state,
    _proposal_strategy_candidates,
    _runtime_model_capabilities,
    _safe_context_length,
    _strategy_cfg,
)
from agent.routes.tasks.autopilot_task_dispatcher_helpers import (
    TaskDispatchResult,
    _current_task_status,
    _dispatch_one_task,
    _effective_agent_cfg_for_task,
    _ensure_llm_profile_snapshot,
    _fallback_policy,
    _is_terminal_status,
    _is_transient_worker_transport_error,
    _maybe_recover_planned_goal_without_candidates,
    _merged_last_proposal_snapshot,
    _recent_strategy_attempts,
    _resolve_autonomous_repair_budget,
    _resolve_non_executable_terminal_status,
    _should_terminalize_no_executable_strategy,
    _task_log,
)


_is_hub_managed_model_recovery_task = is_hub_managed_model_recovery_task

_TERMINAL_GOAL_STATUSES = {"completed", "failed", "cancelled", "aborted", "timeout", "archived"}
_RETRYABLE_NO_WORKER_REASONS = {
    "assigned_worker_offline",
    "assigned_worker_is_hub_forbidden",
    "hub_self_worker_filtered",
    "no_workers_available",
}
_RECOVERY_ACCEPTED_TERMINAL_STATUSES = {
    "completed",
    "verification_failed",
    "cancelled",
    "aborted",
    "timeout",
    "archived",
    "skipped",
}
_DISPATCH_POLL_SECONDS = 1.0


def _record_tick(loop: Any) -> None:
    loop.last_tick_at = time.time()
    loop.tick_count += 1


def _stop_for_terminal_goal(loop: Any, goal_scope: str | None) -> dict[str, Any] | None:
    """Stop goal-scoped loops once the goal is terminal.

    Avoids indefinite idle polling and persisted stale loop sessions.
    """
    if not goal_scope:
        return None
    goal = get_repository_registry(loop._app).goal_repo.get_by_id(goal_scope)
    goal_status = str(getattr(goal, "status", "") or "").strip().lower() if goal else ""
    if goal_status not in _TERMINAL_GOAL_STATUSES:
        return None
    _record_tick(loop)
    try:
        loop.stop(persist=True)
    except Exception:
        if not os.environ.get("PYTEST_CURRENT_TEST"):
            loop._persist_state(enabled=loop.running)
    return {"dispatched": 0, "reason": f"goal_terminal_{goal_status}"}


def _append_dependency_transitions(transitions, append_trace_event: Callable[..., None]) -> None:
    for transition in transitions:
        task_id = str(transition.get("task_id") or "")
        if not task_id:
            continue
        append_trace_event(
            task_id,
            str(transition.get("event_type") or "dependency_state_changed"),
            depends_on=transition.get("depends_on") or [],
            reason=transition.get("reason"),
            failed_dependency_ids=transition.get("failed_dependency_ids") or [],
        )


def _no_candidates_result(
    loop: Any, services: Any, *, all_tasks: list[Any], goal_scope: str | None, task_counts: tuple[int, int]
) -> dict[str, Any]:
    # APR-002: autonomous planning recovery — trigger without requiring UI polling
    if goal_scope and not all_tasks:
        stalled_goal = get_repository_registry(loop._app).goal_repo.get_by_id(goal_scope)
        if stalled_goal and str(getattr(stalled_goal, "status", "") or "").strip().lower() == "planning":
            from agent.services.lifecycle_service import get_goal_lifecycle_service
            get_goal_lifecycle_service().recover_stalled_planning_goal(stalled_goal)
    recovered = _maybe_recover_planned_goal_without_candidates(
        loop=loop,
        services=services,
        all_tasks=all_tasks,
        goal_scope=goal_scope,
    )
    workers_online = services.autopilot_support_service.available_workers(
        team_id=loop.team_id or None,
        is_worker_circuit_open=lambda _url: False,
        app_config=loop._app_config(),
        app=loop._app,
    )[1]
    no_candidate_reason = classify_no_candidate_reason(all_tasks=all_tasks, workers_available_count=workers_online)
    _record_tick(loop)
    loop._persist_state(enabled=loop.running)
    return {
        "dispatched": 0,
        "reason": "goal_recovery_triggered" if recovered else "no_candidates",
        "no_candidate_reason": no_candidate_reason,
        "debug": build_tick_debug_payload(
            team_id_scope=loop.team_id or None,
            total_tasks_unfiltered=task_counts[0],
            total_tasks_scoped=task_counts[1],
            candidate_count=0,
            workers_online_count=workers_online,
            workers_available_count=0,
            no_candidate_reason=no_candidate_reason,
        ),
    }


def _no_workers_result(
    loop: Any, *, candidate_count: int, workers_online_count: int, task_counts: tuple[int, int]
) -> dict[str, Any]:
    loop.last_error = "no_available_workers"
    _record_tick(loop)
    loop._persist_state(enabled=loop.running)
    return {
        "dispatched": 0,
        "reason": "no_available_workers",
        "debug": build_tick_debug_payload(
            team_id_scope=loop.team_id or None,
            total_tasks_unfiltered=task_counts[0],
            total_tasks_scoped=task_counts[1],
            candidate_count=candidate_count,
            workers_online_count=workers_online_count,
            workers_available_count=0,
        ),
    }


def _ollama_capacity(loop: Any, runtime_caps: dict) -> int | None:
    try:
        parallel_cfg = ((loop._agent_config() or {}).get("worker_parallelism") or {}).get("ollama") or {}
        max_parallel = int((parallel_cfg.get("model_defaults") or {}).get("max_parallel_requests") or 0)
        if max_parallel > 0:
            return max_parallel
        ollama_rt = dict((runtime_caps.get("runtime") or {}).get("ollama") or {})
        if ollama_rt.get("ok"):
            return max(1, int(ollama_rt.get("candidate_count") or 1))
    except Exception:
        return None
    return None


def _capacity_factors(loop: Any, workers: list[Any], runtime_caps: dict) -> tuple[int, int, int | None]:
    """Return ``(online_worker_capacity, runtime_capacity, ollama_capacity)``."""
    worker_parallel_cfg = ((loop._agent_config() or {}).get("worker_parallelism") or {}).get("ollama") or {}
    worker_parallelism = max(1, int((worker_parallel_cfg.get("model_defaults") or {}).get("max_parallel_requests") or 1))
    online_worker_capacity = max(1, len(workers)) * worker_parallelism
    runtime_capacity = max(1, int((loop._agent_config() or {}).get("runtime_capacity_cap") or online_worker_capacity))
    return online_worker_capacity, runtime_capacity, _ollama_capacity(loop, runtime_caps)


def _assign_workers(
    loop: Any,
    candidates: list[Any],
    workers: list[Any],
    *,
    append_trace_event: Callable[..., None],
    update_local_task_status: Callable[..., None],
) -> list[tuple[Any, Any, bool]]:
    """thr-010: pre-assign workers sequentially under _routing_lock BEFORE spawning threads.

    Two threads can never receive the same worker slot.
    """
    task_assignments: list[tuple[Any, Any, bool]] = []
    for task in candidates:
        assign_result = loop._assign_worker(task, workers)
        if isinstance(assign_result, tuple) and len(assign_result) >= 3:
            target_worker, was_assigned, assign_reason = assign_result[0], assign_result[1], assign_result[2]
        else:
            target_worker, was_assigned = assign_result
            assign_reason = str(getattr(task, "_worker_assign_reason", "") or "")
        append_trace_event(
            task.id,
            "worker_selection_decision",
            selected_worker=(getattr(target_worker, "url", None) if target_worker is not None else None),
            candidate_count=len(workers),
            rejected_candidates=list(getattr(task, "_worker_policy_rejections", None) or []),
            reason_code=assign_reason or ("assigned_worker" if not was_assigned else "round_robin"),
            was_assigned=bool(was_assigned),
        )
        if target_worker is not None:
            task_assignments.append((task, target_worker, was_assigned))
            continue
        no_worker_reason = str(assign_reason or "no_worker_available")
        retryable = no_worker_reason in _RETRYABLE_NO_WORKER_REASONS
        if not retryable:
            loop._increment_failed()
        append_trace_event(task.id, "autopilot_no_worker", reason=no_worker_reason)
        update_local_task_status(
            task.id,
            "todo" if retryable else "failed",
            error=no_worker_reason,
            event_type="autopilot_no_worker",
            event_actor="autopilot_tick",
            force=True,
        )
    return task_assignments


def _observe_queue_wait(task_assignments: list[tuple[Any, Any, bool]]) -> None:
    for task, _target_worker, _was_assigned in task_assignments:
        try:
            created_at = float(getattr(task, "created_at", 0) or 0)
            if created_at > 0:
                TASK_QUEUE_WAIT_SECONDS.observe(max(0.0, time.time() - created_at))
        except Exception:
            pass


def _cancel_task_requests(tid: str) -> Any:
    from agent.services.request_cancellation_service import (
        get_request_cancellation_service,
    )

    return get_request_cancellation_service().cancel_task_requests(task_id=tid, include_workers=True)


def _abort_recovery_dispatch(
    tid: str, *, reason: str, recoverable: bool, app: Any, append_trace_event: Callable[..., None]
) -> TaskDispatchResult:
    try:
        _cancel_task_requests(tid)
    except Exception:
        logging.exception("Recovery dispatch cancellation failed for %s", tid)
    recovery_reason_code = "recovery_dispatch_stopped" if recoverable else "recovery_dispatch_hard_timeout"
    desired_status = "paused" if recoverable else "failed"
    recovery_status = (
        get_recovery_dispatch_gate_service().abort_dispatch_lease(
            tid,
            target_status=desired_status,
            reason_code=recovery_reason_code,
            error=f"dispatch_{reason}",
            app=app,
        )
        or desired_status
    )
    append_trace_event(
        tid,
        (
            "recovery_dispatch_abort_lost_race"
            if recovery_status in _RECOVERY_ACCEPTED_TERMINAL_STATUSES
            else "recovery_dispatch_aborted"
        ),
        reason=reason,
        status=recovery_status,
    )
    return TaskDispatchResult(
        task_id=tid,
        completed=recovery_status == "completed",
        failed=recovery_status not in {"completed", "paused"},
        failure_type=None if recovery_status == "completed" else recovery_reason_code,
    )


def _abort_pending_dispatch(
    tid: str,
    *,
    reason: str,
    app: Any,
    append_trace_event: Callable[..., None],
    update_local_task_status: Callable[..., None],
) -> TaskDispatchResult:
    recoverable = reason == "stop_event"
    authoritative_task = get_repository_registry(app).task_repo.get_by_id(tid)
    if _is_hub_managed_model_recovery_task(authoritative_task):
        return _abort_recovery_dispatch(
            tid, reason=reason, recoverable=recoverable, app=app, append_trace_event=append_trace_event
        )
    logging.warning(
        "[tick][task_id=%s] dispatch aborted (%s), marking %s",
        tid, reason, "todo" if recoverable else "failed",
    )
    # the abandoned thread keeps waiting on its model call; stop that call on the Hub and the
    # workers, otherwise the model server keeps generating for nobody and blocks a slot
    try:
        cancelled = _cancel_task_requests(tid)
        append_trace_event(tid, "dispatch_requests_cancelled", reason=reason,
                           result=cancelled if isinstance(cancelled, dict) else None)
    except Exception:
        logging.exception("Dispatch cancellation failed for %s", tid)
    update_local_task_status(tid, "todo" if recoverable else "failed", error=f"dispatch_{reason}", force=True)
    append_trace_event(tid, "dispatch_aborted", reason=reason)
    return TaskDispatchResult(
        task_id=tid,
        failed=not recoverable,
        failure_type=("dispatch_aborted" if not recoverable else "recoverable_dispatch_aborted"),
    )


def _await_dispatches(
    loop: Any,
    future_to_task_id: dict[concurrent.futures.Future, str],
    *,
    per_task_hard_timeout: int,
    app: Any,
    append_trace_event: Callable[..., None],
    update_local_task_status: Callable[..., None],
) -> list[TaskDispatchResult]:
    """Collect finished dispatches until the hard timeout or stop event, then abort the rest."""
    task_results: list[TaskDispatchResult] = []
    pending = set(future_to_task_id.keys())
    timeout_at = time.time() + per_task_hard_timeout
    while pending and time.time() < timeout_at:
        if loop._stop_event.is_set():
            break
        done, pending = concurrent.futures.wait(
            pending, timeout=_DISPATCH_POLL_SECONDS,
            return_when=concurrent.futures.FIRST_COMPLETED,
        )
        for future in done:
            tid = future_to_task_id[future]
            try:
                task_results.append(future.result())
            except Exception as exc:
                logging.error("[tick][task_id=%s] _dispatch_one_task raised: %s", tid, exc)
                update_local_task_status(tid, "failed", error=str(exc), force=True)
                task_results.append(TaskDispatchResult(task_id=tid, failed=True, failure_type="thread_exception"))
    # Cancel any remaining pending futures (timeout or stop_event).
    for future in pending:
        tid = future_to_task_id[future]
        future.cancel()
        reason = "stop_event" if loop._stop_event.is_set() else f"hard_timeout_{per_task_hard_timeout}s"
        task_results.append(
            _abort_pending_dispatch(
                tid,
                reason=reason,
                app=app,
                append_trace_event=append_trace_event,
                update_local_task_status=update_local_task_status,
            )
        )
    return task_results


def _aggregate_results(loop: Any, task_results: list[TaskDispatchResult]) -> tuple[int, int, int, list[str]]:
    """thr-012: aggregate into local counters + loop counters (thr-002: via _increment_*)."""
    dispatched = completed = failed = 0
    dispatched_task_ids: list[str] = []
    for r in task_results:
        if r.dispatched:
            loop._increment_dispatched()
            dispatched += 1
            dispatched_task_ids.append(r.task_id)
            if r.completed:
                loop._increment_completed()
                completed += 1
            else:
                loop._increment_failed()
                failed += 1
        elif r.failed:
            loop._increment_failed()
            failed += 1
    return dispatched, completed, failed, dispatched_task_ids


def execute_autopilot_tick(
    *,
    loop: Any,
    services: Any,
    append_trace_event: Callable[..., None],
    task_dependencies: Callable[[Any], list[str]],
    update_local_task_status: Callable[..., None],
) -> dict[str, Any]:
    if settings.role != "hub":
        return {"dispatched": 0, "reason": "hub_only"}
    if loop.running:
        guardrail_reason = loop._check_guardrails()
        if guardrail_reason:
            loop.last_error = guardrail_reason
            loop.stop(persist=True)
            return {"dispatched": 0, "reason": guardrail_reason}

    goal_scope = str(getattr(loop, "goal", "") or "").strip() or None
    terminal_goal_result = _stop_for_terminal_goal(loop, goal_scope)
    if terminal_goal_result is not None:
        return terminal_goal_result

    total_tasks_unfiltered = len(services.autopilot_support_service.scoped_tasks(team_id=None, app=loop._app))
    all_tasks = services.autopilot_support_service.scoped_tasks(team_id=loop.team_id or None, app=loop._app)
    if goal_scope:
        all_tasks = [task for task in all_tasks if str(getattr(task, "goal_id", "") or "").strip() == goal_scope]
    task_counts = (total_tasks_unfiltered, len(all_tasks))
    StaleTaskRecovery(
        loop=loop,
        append_trace_event=append_trace_event,
        update_local_task_status=update_local_task_status,
        approval_lifecycle=refresh_approval_lifecycle(),
        now_ts=time.time(),
    ).run(all_tasks)

    _append_dependency_transitions(
        services.task_queue_service.reconcile_dependencies(tasks=all_tasks, dependency_resolver=task_dependencies),
        append_trace_event,
    )
    dispatch_queue = services.task_queue_service.get_scoped_dispatch_queue(team_id=loop.team_id or None, now=time.time())
    if goal_scope:
        dispatch_queue = [
            item
            for item in dispatch_queue
            if str(getattr(item.get("task"), "goal_id", "") or "").strip() == goal_scope
        ]
    candidates = [item["task"] for item in dispatch_queue if item.get("task") is not None]
    if not candidates:
        return _no_candidates_result(
            loop, services, all_tasks=all_tasks, goal_scope=goal_scope, task_counts=task_counts
        )

    workers, workers_online_count = services.autopilot_support_service.available_workers(
        team_id=loop.team_id or None,
        is_worker_circuit_open=loop._is_worker_circuit_open,
        app_config=loop._app_config(),
        app=loop._app,
    )
    if not workers:
        return _no_workers_result(
            loop, candidate_count=len(candidates), workers_online_count=workers_online_count, task_counts=task_counts
        )

    policy = loop._security_policy()
    fallback_policy = _fallback_policy(loop)
    runtime_caps = _runtime_model_capabilities(loop)
    online_worker_capacity, runtime_capacity, ollama_capacity = _capacity_factors(loop, workers, runtime_caps)
    effective_concurrency = resolve_effective_concurrency(
        requested_max_concurrency=loop.max_concurrency,
        security_policy=policy,
        online_worker_capacity=online_worker_capacity,
        runtime_capacity=runtime_capacity,
        ollama_capacity=ollama_capacity,
    )
    local_worker_url = (settings.agent_url or f"http://localhost:{settings.port}").rstrip("/")
    queue_positions = dispatch_queue_positions(dispatch_queue)
    task_assignments = _assign_workers(
        loop,
        candidates[:effective_concurrency],
        workers,
        append_trace_event=append_trace_event,
        update_local_task_status=update_local_task_status,
    )

    # thr-011: propose_timeout + execute_timeout + 30s buffer = hard deadline per task thread.
    per_task_hard_timeout = resolve_dispatch_hard_timeout(
        tasks=[task for task, _target_worker, _was_assigned in task_assignments],
        security_policy=policy,
    )
    app = loop._app

    # thr-006: parallel dispatch via ThreadPoolExecutor.
    # thr-015: executor.shutdown(wait=False) so the per-goal tick lock is
    #          released immediately when _stop_event is set. Running threads
    #          continue in the background and update task status on completion.
    # thr-016: per-goal tick tracking (autopilot.py) replaces _tick_lock. Different
    #          goals can tick in parallel; the same goal is guarded by _active_goal_ticks.
    # Dispatch stays synchronous (deterministic and state-safe) until async mode
    # is hardened end-to-end.
    dispatch_window_started = time.time()
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=max(1, effective_concurrency))
    try:
        _observe_queue_wait(task_assignments)
        future_to_task_id: dict[concurrent.futures.Future, str] = {
            executor.submit(
                _dispatch_one_task,
                task=task,
                target_worker=target_worker,
                was_assigned=was_assigned,
                loop=loop,
                services=services,
                policy=policy,
                fallback_policy=fallback_policy,
                runtime_caps=runtime_caps,
                queue_positions=queue_positions,
                local_worker_url=local_worker_url,
                app=app,
                append_trace_event=append_trace_event,
                update_local_task_status=update_local_task_status,
            ): task.id
            for task, target_worker, was_assigned in task_assignments
        }
        task_results = _await_dispatches(
            loop,
            future_to_task_id,
            per_task_hard_timeout=per_task_hard_timeout,
            app=app,
            append_trace_event=append_trace_event,
            update_local_task_status=update_local_task_status,
        )
    finally:
        executor.shutdown(wait=False)
        DISPATCH_WAIT_SECONDS.observe(max(0.0, time.time() - dispatch_window_started))

    dispatched, completed, failed, dispatched_task_ids = _aggregate_results(loop, task_results)
    loop.last_tick_at = time.time()
    loop._set_last_error(None)
    loop._increment_tick_count()
    loop._persist_state(enabled=loop.running)
    # Wake the loop immediately if there may be more tasks ready (sequential chains).
    if dispatched > 0:
        try:
            loop.wake()
        except Exception:
            pass
    return {
        "dispatched": dispatched,
        "completed": completed,
        "failed": failed,
        "task_ids": dispatched_task_ids,
        "reason": "ok",
        "debug": build_tick_debug_payload(
            team_id_scope=loop.team_id or None,
            total_tasks_unfiltered=total_tasks_unfiltered,
            total_tasks_scoped=task_counts[1],
            candidate_count=len(candidates),
            workers_online_count=workers_online_count,
            workers_available_count=len(workers),
        ),
        "effective_concurrency_factors": {
            "requested": int(loop.max_concurrency),
            "security_cap": int((policy or {}).get("max_concurrency_cap") or 1),
            "online_worker_capacity": int(online_worker_capacity),
            "runtime_capacity": int(runtime_capacity),
            "ollama_capacity": int(ollama_capacity) if ollama_capacity is not None else None,
            "effective_concurrency": int(effective_concurrency),
        },
    }
