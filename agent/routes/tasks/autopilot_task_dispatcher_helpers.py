from __future__ import annotations

import contextlib
import logging
import time
from dataclasses import dataclass
from typing import Any, Callable

from agent.config import settings
from agent.metrics import (
    TASK_FAILURE_REASON_COUNT,
    TASK_SUCCESS_RATE,
    WORKER_BUSY_SECONDS,
    WORKSPACE_WRITE_CONFLICT_COUNT,
)
from agent.services.goal_config_runtime_service import get_goal_config_runtime_service
from agent.services.repository_registry import get_repository_registry


@dataclass
class TaskDispatchResult:
    task_id: str
    dispatched: bool = False
    completed: bool = False
    failed: bool = False
    failure_type: str | None = None


def _current_task_status(task_id: str, *, app: Any) -> str:
    try:
        repos = get_repository_registry(app)
        current = repos.task_repo.get_by_id(task_id)
    except Exception:
        return ""
    return str(getattr(current, "status", "") or "").strip().lower()


def _is_terminal_status(status: str) -> bool:
    return status in {"completed", "failed", "cancelled"}


def _should_terminalize_no_executable_strategy(strategy_failures: list[dict[str, Any]]) -> bool:
    failure_types = {
        str(item.get("failure_type") or "").strip().lower()
        for item in list(strategy_failures or [])
        if isinstance(item, dict)
    }
    return bool(failure_types & {"invalid_proposal", "no_executable_step", "proposal_budget_exhausted"})


def _resolve_non_executable_terminal_status(*, agent_cfg: dict[str, Any]) -> str:
    propose_policy_cfg = dict((agent_cfg.get("propose_policy") or {}))
    allow_human_review = bool(propose_policy_cfg.get("allow_human_review", True))
    on_declined = str(propose_policy_cfg.get("on_all_strategies_declined") or "needs_review").strip().lower()
    if on_declined == "failed":
        return "failed"
    if on_declined == "advisory":
        return "todo"
    return "needs_review" if allow_human_review else "failed"


def _effective_agent_cfg_for_task(*, loop: Any, task: Any) -> dict[str, Any]:
    base_cfg = dict(loop._agent_config() or {})
    goal_id = str(getattr(task, "goal_id", "") or "").strip()
    if not goal_id:
        return base_cfg
    try:
        scoped = get_goal_config_runtime_service().get_effective_config(
            goal_id=goal_id,
            task_id=str(getattr(task, "id", "") or "").strip() or None,
        )
        scoped_cfg = dict(scoped.config or {})
        if scoped_cfg:
            return scoped_cfg
    except Exception:
        logging.debug("autopilot_goal_scoped_config_resolution_failed", exc_info=True)
    return base_cfg


def _resolve_autonomous_repair_budget(*, agent_cfg: dict[str, Any]) -> tuple[int, int]:
    propose_policy = dict((agent_cfg or {}).get("propose_policy") or {})
    attempts = propose_policy.get("autonomous_repair_attempts", propose_policy.get("max_repair_attempts", 2))
    delay = propose_policy.get("autonomous_repair_delay_seconds", 8)
    try:
        attempts_i = max(0, min(int(attempts), 5))
    except (TypeError, ValueError):
        attempts_i = 2
    try:
        delay_i = max(0, min(int(delay), 120))
    except (TypeError, ValueError):
        delay_i = 8
    return attempts_i, delay_i


def _recent_strategy_attempts(task: Any, *, now_ts: float, window_seconds: int) -> int:
    if window_seconds <= 0:
        return 0
    threshold = now_ts - float(window_seconds)
    count = 0
    for entry in list(getattr(task, "history", None) or []):
        if not isinstance(entry, dict):
            continue
        if str(entry.get("event_type") or "") != "autopilot_strategy_attempt":
            continue
        try:
            ts = float(entry.get("timestamp") or 0.0)
        except (TypeError, ValueError):
            continue
        if ts >= threshold:
            count += 1
    return count


def _is_transient_worker_transport_error(exc: Exception) -> bool:
    text = str(exc or "").strip().lower()
    if not text:
        return False
    markers = (
        "connection reset by peer",
        "remote end closed connection",
        "failed to establish a new connection",
        "max retries exceeded",
        "connection refused",
        "read timed out",
        "connect timeout",
        "temporarily unavailable",
    )
    return any(marker in text for marker in markers)


def _merged_last_proposal_snapshot(*, task_id: str, snapshot: dict[str, Any], app: Any) -> dict[str, Any]:
    repos = get_repository_registry(app)
    current = repos.task_repo.get_by_id(task_id)
    existing = dict(getattr(current, "last_proposal", None) or {})
    merged = {**existing, **dict(snapshot or {})}
    return merged


def _ensure_llm_profile_snapshot(
    *,
    snapshot: dict[str, Any],
    strategy_id: str | None,
    model_meta: dict[str, Any] | None,
    preferred_profile: list[dict[str, Any]] | None = None,
    allow_synthetic_fallback: bool = True,
) -> dict[str, Any]:
    updated = dict(snapshot or {})
    cli_result = updated.get("cli_result")
    if not isinstance(cli_result, dict):
        cli_result = {}
    profile = list(cli_result.get("llm_call_profile") or [])
    has_profile = any(isinstance(entry, dict) for entry in profile)
    if has_profile:
        updated["cli_result"] = cli_result
        return updated
    preferred = [dict(entry) for entry in list(preferred_profile or []) if isinstance(entry, dict)]
    if preferred:
        cli_result["llm_call_profile"] = preferred
        if "latency_ms" not in cli_result:
            cli_result["latency_ms"] = None
        if "returncode" not in cli_result:
            cli_result["returncode"] = 0
        backend_prefill = str(updated.get("backend") or "").strip() or "orchestrator"
        if "output_source" not in cli_result:
            cli_result["output_source"] = backend_prefill
        updated["cli_result"] = cli_result
        return updated
    if not allow_synthetic_fallback:
        updated["cli_result"] = cli_result
        return updated
    provider = None
    model = None
    if isinstance(model_meta, dict):
        provider = str(model_meta.get("runtime_provider") or "").strip() or None
        model = str(model_meta.get("selected_model") or "").strip() or None
    backend = str(updated.get("backend") or "").strip() or "orchestrator"
    cli_result["llm_call_profile"] = [
        {
            "name": f"propose_{str(strategy_id or 'orchestrator').strip() or 'orchestrator'}",
            "backend": backend,
            "provider": provider,
            "model": model,
            "success": True,
            "latency_ms": None,
            "prompt_tokens": None,
            "completion_tokens": None,
            "total_tokens": None,
            "source": "orchestrator_synthetic",
            "estimated": True,
            "error_type": None,
            "error_message": None,
            "started_at": None,
            "ended_at": None,
        }
    ]
    if "latency_ms" not in cli_result:
        cli_result["latency_ms"] = None
    if "returncode" not in cli_result:
        cli_result["returncode"] = 0
    if "output_source" not in cli_result:
        cli_result["output_source"] = backend
    updated["cli_result"] = cli_result
    return updated


def _fallback_policy(loop: Any) -> dict[str, Any]:
    cfg = (loop._agent_config() or {}).get("execution_fallback_policy", {}) or {}
    return {
        "allow_hub_worker_fallback": bool(cfg.get("allow_hub_worker_fallback", True)),
        "escalate_on_fallback_block": bool(cfg.get("escalate_on_fallback_block", True)),
        "fallback_block_status": str(cfg.get("fallback_block_status") or "blocked").strip().lower() or "blocked",
    }


def _maybe_recover_planned_goal_without_candidates(*, loop: Any, services: Any, all_tasks: list[Any], goal_scope: str | None) -> bool:
    goal_id = str(goal_scope or "").strip()
    if not goal_id:
        return False
    repos = get_repository_registry(loop._app)
    goal = repos.goal_repo.get_by_id(goal_id)
    if goal is None:
        return False
    goal_status = str(getattr(goal, "status", "") or "").strip().lower()
    if goal_status not in {"planning", "planned"}:
        return False
    goal_tasks_global = [
        task
        for task in repos.task_repo.get_all()
        if str(getattr(task, "goal_id", "") or "").strip() == goal_id
    ]
    task_view = list(all_tasks or [])
    if not task_view and goal_tasks_global:
        task_view = list(goal_tasks_global)
    non_terminal = [
        task
        for task in task_view
        if not _is_terminal_status(str(getattr(task, "status", "") or "").strip().lower())
    ]
    active_statuses = {"assigned", "proposing", "in_progress"}
    has_active = any(str(getattr(task, "status", "") or "").strip().lower() in active_statuses for task in non_terminal)
    if has_active:
        return False
    has_blocked = any(str(getattr(task, "status", "") or "").strip().lower() == "blocked_by_dependency" for task in non_terminal)
    if has_blocked:
        return False
    has_todo = any(str(getattr(task, "status", "") or "").strip().lower() in {"todo", "created"} for task in non_terminal)
    if has_todo:
        return False
    has_review_pending = any(
        str(getattr(task, "status", "") or "").strip().lower() in {"waiting_for_review", "needs_review"}
        for task in non_terminal
    )
    if has_review_pending:
        return False
    if task_view and not non_terminal:
        all_failed = all(
            str(getattr(t, "status", "") or "").strip().lower() == "failed" for t in task_view
        )
        if all_failed:
            try:
                services.goal_lifecycle_service.transition_goal(
                    goal, target_status="failed", reason="all_tasks_failed_no_recovery",
                )
            except Exception:
                pass
            return False
    now_ts = time.time()
    execution_preferences = dict(getattr(goal, "execution_preferences", None) or {})
    recovery = dict(execution_preferences.get("autopilot_recovery") or {})
    last_attempt_at = float(recovery.get("last_attempt_at") or 0.0)
    attempts = int(recovery.get("attempts") or 0)
    max_attempts = 2
    cooldown_seconds = 45
    if attempts >= max_attempts or (last_attempt_at and (now_ts - last_attempt_at) < cooldown_seconds):
        stall_since = float(recovery.get("stall_since") or 0.0) or now_ts
        recovery.setdefault("stall_since", stall_since)
        stalled_for = max(0.0, now_ts - stall_since)
        stale_threshold_seconds = 120.0
        if attempts >= max_attempts and stalled_for >= stale_threshold_seconds:
            try:
                services.goal_lifecycle_service.transition_goal(
                    goal, target_status="failed", reason="planned_stall_no_dispatchable_candidates",
                )
                recovery.update({"last_attempt_at": now_ts, "last_reason": "planned_stall_no_dispatchable_candidates", "stalled_for_seconds": int(stalled_for)})
                execution_preferences["autopilot_recovery"] = recovery
                goal.execution_preferences = execution_preferences
                repos.goal_repo.save(goal)
            except Exception:
                pass
        else:
            recovery.update({"last_attempt_at": now_ts, "last_reason": "recovery_cooldown_or_exhausted", "stalled_for_seconds": int(stalled_for)})
            execution_preferences["autopilot_recovery"] = recovery
            goal.execution_preferences = execution_preferences
            repos.goal_repo.save(goal)
        return False
    recovery_reason = "no_nonterminal_tasks" if not non_terminal else "no_dispatchable_candidates"
    recovery.setdefault("stall_since", now_ts)
    try:
        from agent.routes.tasks.auto_planner import auto_planner
        team_id = str(getattr(goal, "team_id", "") or "").strip() or None
        context = getattr(goal, "context", None)
        plan_result = auto_planner.plan_goal(
            goal=str(getattr(goal, "goal", "") or ""),
            context=context if isinstance(context, str) else None,
            team_id=team_id, create_tasks=True, use_template=True, use_repo_context=True,
            goal_id=goal.id, goal_trace_id=str(getattr(goal, "trace_id", "") or ""),
            mode=str(getattr(goal, "mode", "") or "generic"),
            mode_data=dict(getattr(goal, "mode_data", None) or {}),
        )
        created_ids = list((plan_result or {}).get("created_task_ids") or [])
        recovery.update({"attempts": attempts + 1, "last_attempt_at": now_ts, "last_reason": recovery_reason, "last_created_task_count": len(created_ids), "last_plan_id": (plan_result or {}).get("plan_id")})
        execution_preferences["autopilot_recovery"] = recovery
        goal.execution_preferences = execution_preferences
        repos.goal_repo.save(goal)
        if created_ids:
            recovery["stall_since"] = now_ts
            with contextlib.suppress(Exception):
                loop.wake()
            return True
    except Exception as exc:
        recovery.update({"attempts": attempts + 1, "last_attempt_at": now_ts, "last_reason": f"{recovery_reason}:error", "last_error": str(exc)[:240]})
        execution_preferences["autopilot_recovery"] = recovery
        goal.execution_preferences = execution_preferences
        repos.goal_repo.save(goal)
    return False


def _task_log(task_id: str) -> logging.LoggerAdapter:
    return logging.LoggerAdapter(logging.getLogger(__name__), {"task_id": task_id})


def _execute_proposed_step(
    *,
    task: Any,
    command: str | None,
    tool_calls: list[dict[str, Any]] | None,
    policy: dict[str, Any],
    loop: Any,
    target_worker: Any,
    result: TaskDispatchResult,
    append_trace_event: Callable[..., None],
    update_local_task_status: Callable[..., None],
    services: Any,
    proposal_snapshot: dict[str, Any],
    model_meta: dict[str, Any],
    app_ctx: Any,
    log: logging.LoggerAdapter,
) -> TaskDispatchResult:
    execute_payload = {
        "task_id": task.id,
        "command": command,
        "tool_calls": tool_calls,
        "timeout": int(policy["execute_timeout"]),
        "retries": int(policy["execute_retries"]),
    }
    try:
        latest_status = _current_task_status(task.id, app=app_ctx)
        if _is_terminal_status(latest_status):
            append_trace_event(
                task.id,
                "autopilot_execute_skipped_terminal",
                delegated_to=target_worker.url,
                terminal_status=latest_status,
            )
            result.dispatched = True
            result.completed = latest_status == "completed"
            result.failed = latest_status != "completed"
            result.failure_type = None if result.completed else latest_status
            return result
        _execute_started = time.time()
        execute_data = loop._forward_with_retry(
            target_worker.url,
            f"/tasks/{task.id}/step/execute",
            execute_payload,
            token=target_worker.token,
        )
        WORKER_BUSY_SECONDS.observe(max(0.0, time.time() - _execute_started))
    except Exception as e:
        if _is_transient_worker_transport_error(e):
            defer_until = time.time() + 30
            update_local_task_status(
                task.id,
                "todo",
                manual_override_until=defer_until,
                error=f"transient_worker_transport_error:{str(e)[:180]}",
                last_proposal=_merged_last_proposal_snapshot(task_id=task.id, snapshot=proposal_snapshot, app=app_ctx),
            )
            append_trace_event(
                task.id,
                "autopilot_worker_transport_deferred",
                delegated_to=target_worker.url,
                reason=str(e),
                defer_seconds=30,
            )
            result.failed = True
            result.failure_type = "execute_transport_deferred"
            return result
        latest_status = _current_task_status(task.id, app=app_ctx)
        if _is_terminal_status(latest_status):
            append_trace_event(
                task.id,
                "autopilot_execute_failed_skipped_terminal",
                delegated_to=target_worker.url,
                terminal_status=latest_status,
                reason=str(e),
            )
            result.dispatched = True
            result.completed = latest_status == "completed"
            result.failed = latest_status != "completed"
            result.failure_type = None if result.completed else latest_status
            return result
        update_local_task_status(task.id, "failed", error=str(e))
        append_trace_event(task.id, "autopilot_worker_failed", delegated_to=target_worker.url, reason=str(e))
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
        result.failure_type = "execute_exception"
        return result

    task_status, exit_code, output = services.autopilot_decision_service.normalize_execute_result(execute_data)
    task_status, output, quality_gate_reason = services.autopilot_decision_service.apply_quality_gate_if_needed(
        task=task,
        task_status=task_status,
        output=output,
        exit_code=exit_code,
        agent_cfg=loop._agent_config(),
    )
    latest_status = _current_task_status(task.id, app=app_ctx)
    if _is_terminal_status(latest_status):
        append_trace_event(
            task.id,
            "autopilot_result_skipped_terminal",
            delegated_to=target_worker.url,
            terminal_status=latest_status,
            attempted_status=task_status,
            exit_code=exit_code,
        )
        result.dispatched = True
        result.completed = latest_status == "completed"
        result.failed = latest_status != "completed"
        result.failure_type = None if result.completed else latest_status
        return result
    if quality_gate_reason:
        append_trace_event(
            task.id,
            "quality_gate_failed",
            reason=quality_gate_reason,
            delegated_to=target_worker.url,
        )
    update_local_task_status(
        task.id,
        task_status,
        last_output=output,
        last_exit_code=exit_code,
        last_proposal=_merged_last_proposal_snapshot(task_id=task.id, snapshot=proposal_snapshot, app=app_ctx),
    )
    append_trace_event(
        task.id,
        "autopilot_result",
        delegated_to=target_worker.url,
        status=task_status,
        exit_code=exit_code,
        output_preview=(output or "")[:220],
        backend=proposal_snapshot.get("backend"),
        routing_reason=((proposal_snapshot.get("routing") or {}).get("reason")),
    )
    append_trace_event(
        task.id,
        "workspace_released",
        delegated_to=target_worker.url,
        workspace_id=f"ws-{task.id}",
        lease_id=f"lease-{task.id}",
        cleanup_state="completed" if task_status == "completed" else "failed",
    )

    result.dispatched = True
    result.completed = task_status == "completed"
    result.failed = task_status != "completed"
    result.failure_type = None if task_status == "completed" else task_status
    if result.completed:
        TASK_SUCCESS_RATE.inc()
    else:
        TASK_FAILURE_REASON_COUNT.labels(reason=str(result.failure_type or "unknown")).inc()
        if str(result.failure_type or "") in {"output_dir_busy", "workspace_write_conflict"}:
            WORKSPACE_WRITE_CONFLICT_COUNT.inc()

    if result.completed:
        try:
            from agent.routes.tasks.auto_planner import auto_planner
            if auto_planner.auto_followup_enabled:
                auto_planner.analyze_and_create_followups(
                    task_id=task.id,
                    output=output,
                    exit_code=exit_code,
                )
        except Exception as e:
            log.debug("Followup analysis skipped: %s", e)

    return result


def _dispatch_one_task(
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
    app: Any,
    append_trace_event: Callable[..., None],
    update_local_task_status: Callable[..., None],
) -> TaskDispatchResult:
    from agent.services.lmstudio_request_registry import set_thread_context, clear_thread_context
    _goal_id = str(getattr(task, "goal_id", "") or "").strip() or None
    _task_id = str(getattr(task, "id", "") or "").strip() or None
    set_thread_context(_goal_id, _task_id)
    try:
        _ctx = app.app_context() if app is not None else contextlib.nullcontext()
        with _ctx:
            from agent.routes.tasks.autopilot_task_dispatcher import _dispatch_one_task_inner
            return _dispatch_one_task_inner(
                task=task, target_worker=target_worker, was_assigned=was_assigned,
                loop=loop, services=services, policy=policy, fallback_policy=fallback_policy,
                runtime_caps=runtime_caps, queue_positions=queue_positions, local_worker_url=local_worker_url,
                append_trace_event=append_trace_event, update_local_task_status=update_local_task_status,
            )
    finally:
        clear_thread_context()
