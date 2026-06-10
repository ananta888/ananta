from __future__ import annotations

import concurrent.futures
import logging
import os
import time
from typing import Any, Callable

from agent.config import settings
from agent.metrics import DISPATCH_WAIT_SECONDS, TASK_QUEUE_WAIT_SECONDS
from agent.services.repository_registry import get_repository_registry
from agent.routes.tasks.autopilot_dispatch_policy import (
    build_tick_debug_payload,
    classify_no_candidate_reason,
    dispatch_queue_positions,
    resolve_effective_concurrency,
    resolve_target_worker_for_task,
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
from agent.routes.tasks.autopilot_task_dispatcher import (
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


def execute_autopilot_tick(
    *,
    loop: Any,
    services: Any,
    append_trace_event: Callable[..., None],
    task_dependencies: Callable[[Any], list[str]],
    update_local_task_status: Callable[..., None],
) -> dict[str, Any]:  # noqa: C901
    if settings.role != "hub":
        return {"dispatched": 0, "reason": "hub_only"}
    if loop.running:
        guardrail_reason = loop._check_guardrails()
        if guardrail_reason:
            loop.last_error = guardrail_reason
            loop.stop(persist=True)
            return {"dispatched": 0, "reason": guardrail_reason}

    goal_scope = str(getattr(loop, "goal", "") or "").strip() or None
    if goal_scope:
        repos = get_repository_registry(loop._app)
        goal = repos.goal_repo.get_by_id(goal_scope)
        goal_status = str(getattr(goal, "status", "") or "").strip().lower() if goal else ""
        if goal_status in {"completed", "failed", "cancelled", "aborted", "timeout"}:
            loop.last_tick_at = time.time()
            loop.tick_count += 1
            # Stop goal-scoped loops once the goal is terminal to avoid
            # indefinite idle polling and persisted stale loop sessions.
            try:
                loop.stop(persist=True)
            except Exception:
                if not os.environ.get("PYTEST_CURRENT_TEST"):
                    loop._persist_state(enabled=loop.running)
            return {"dispatched": 0, "reason": f"goal_terminal_{goal_status}"}

    total_tasks_unfiltered = len(services.autopilot_support_service.scoped_tasks(team_id=None, app=loop._app))
    all_tasks = services.autopilot_support_service.scoped_tasks(team_id=loop.team_id or None, app=loop._app)
    if goal_scope:
        all_tasks = [task for task in all_tasks if str(getattr(task, "goal_id", "") or "").strip() == goal_scope]
    scoped_tasks = len(all_tasks)

    # Reset tasks stuck in `proposing` with no output for > 90 s back to `todo`
    # so the autopilot can retry them (workers can crash mid-dispatch).
    _PROPOSING_STALE_SECONDS = 30
    _ASSIGNED_STALE_SECONDS = 60
    _IN_PROGRESS_STALE_SECONDS = 120
    _RECOVER_WAITING_REVIEW_SECONDS = 30
    now_ts = time.time()
    for _t in all_tasks:
        if str(getattr(_t, "status", "") or "").lower() != "proposing":
            continue
        _updated = float(getattr(_t, "updated_at", None) or 0)
        if _updated and (now_ts - _updated) < _PROPOSING_STALE_SECONDS:
            continue
        if getattr(_t, "last_output", None):
            continue
        update_local_task_status(
            _t.id,
            "todo",
            event_type="stale_proposing_reset",
            event_actor="autopilot_tick",
            force=True,
        )
        append_trace_event(_t.id, "stale_proposing_reset", reason="no_output_after_90s")

    # Recover stale active tasks that stopped progressing without terminal output.
    # This keeps autonomous runs moving when worker transport/runtime hangs.
    for _t in all_tasks:
        _status = str(getattr(_t, "status", "") or "").lower()
        if _status not in {"assigned", "in_progress"}:
            continue
        _updated = float(getattr(_t, "updated_at", None) or 0)
        stale_after = _ASSIGNED_STALE_SECONDS if _status == "assigned" else _IN_PROGRESS_STALE_SECONDS
        if _updated and (now_ts - _updated) < stale_after:
            continue
        _verification = dict(getattr(_t, "verification_status", None) or {})
        _recovery = dict(_verification.get("autopilot_recovery") or {})
        retries = int(_recovery.get("stale_active_retries") or 0)
        max_retries = 3
        if retries < max_retries:
            _recovery.update(
                {
                    "stale_active_retries": retries + 1,
                    "last_stale_active_status": _status,
                    "last_stale_active_retry_at": now_ts,
                }
            )
            _verification["autopilot_recovery"] = _recovery
            update_local_task_status(
                _t.id,
                "todo",
                verification_status=_verification,
                event_type="stale_active_task_retry",
                event_actor="autopilot_tick",
                force=True,
            )
            append_trace_event(
                _t.id,
                "stale_active_task_retry",
                stale_status=_status,
                retry_attempt=retries + 1,
                stale_after_seconds=stale_after,
            )
            continue
        update_local_task_status(
            _t.id,
            "failed",
            error=f"stale_active_task_exhausted:{_status}",
            event_type="stale_active_task_auto_failed",
            event_actor="autopilot_tick",
            force=True,
        )
        append_trace_event(
            _t.id,
            "stale_active_task_auto_failed",
            stale_status=_status,
            retry_attempt=retries,
            stale_after_seconds=stale_after,
        )

    # Auto-recover waiting_for_review tasks caused by recoverable runtime/tooling issues.
    # These are machine-retryable artifacts and should not deadlock the chain.
    # In fully autonomous runs (allow_human_review=False) allow up to autonomous_repair_attempts
    # retries before failing, to allow round-robin assignment to reach a capable worker.
    _TOOLING_RECOVERY_MAX = 2
    for _t in all_tasks:
        if str(getattr(_t, "status", "") or "").lower() != "waiting_for_review":
            continue
        _updated = float(getattr(_t, "updated_at", None) or 0)
        if _updated and (now_ts - _updated) < _RECOVER_WAITING_REVIEW_SECONDS:
            continue
        last_output = str(getattr(_t, "last_output", None) or "")
        lowered = last_output.lower()
        recoverable_waiting_review = (
            "[tool_intent] unresolved:" in last_output
            or "command not found" in lowered
            or "not recognized as an internal or external command" in lowered
            or "no such file or directory" in lowered
        )
        if not recoverable_waiting_review:
            continue
        _task_agent_cfg = _effective_agent_cfg_for_task(loop=loop, task=_t)
        _task_allow_human_review = bool((_task_agent_cfg.get("propose_policy") or {}).get("allow_human_review", True))
        _t_verification = dict(getattr(_t, "verification_status", None) or {})
        _t_recovery = dict(_t_verification.get("autopilot_recovery") or {})
        try:
            _tooling_retries = max(0, int(_t_recovery.get("tooling_retries") or 0))
        except (TypeError, ValueError):
            _tooling_retries = 0
        _attempts_raw = (_task_agent_cfg.get("propose_policy") or {}).get(
            "autonomous_repair_attempts", _TOOLING_RECOVERY_MAX
        )
        try:
            _max_tooling_retries = max(0, int(_attempts_raw))
        except (TypeError, ValueError):
            _max_tooling_retries = _TOOLING_RECOVERY_MAX
        _can_retry = _task_allow_human_review or _tooling_retries < _max_tooling_retries
        if _can_retry and not _task_allow_human_review:
            _t_recovery["tooling_retries"] = _tooling_retries + 1
            _t_recovery["last_tooling_retry_at"] = now_ts
            _t_verification["autopilot_recovery"] = _t_recovery
        _recovery_status = "todo" if _can_retry else "failed"
        _recovery_event = "recover_waiting_review_retryable_failure" if _can_retry else "waiting_for_review_auto_failed_no_human_review"
        update_local_task_status(
            _t.id,
            _recovery_status,
            verification_status=_t_verification if not _task_allow_human_review else None,
            error=None if _can_retry else "autonomous_run_tooling_retries_exhausted",
            event_type=_recovery_event,
            event_actor="autopilot_tick",
            force=True,
        )
        append_trace_event(
            _t.id,
            _recovery_event,
            reason="auto_retry_recoverable_waiting_review_failure" if _can_retry else "autonomous_run_waiting_for_review_terminated",
            allow_human_review=_task_allow_human_review,
            tooling_retries=_tooling_retries,
            max_tooling_retries=_max_tooling_retries,
        )

    # Guardrail: in fully autonomous runs, stale waiting_for_review tasks must
    # not block goal terminalization indefinitely.
    #
    # For strategy/budget guardrails, prefer controlled retry (todo) before
    # hard-failing the task, otherwise autonomous opencode runs can dead-end
    # without ever producing executable steps/artifacts.
    _FORCE_FAIL_WAITING_REVIEW_SECONDS = 90
    _WAITING_REVIEW_RETRY_MAX = 2
    for _t in all_tasks:
        if str(getattr(_t, "status", "") or "").lower() != "waiting_for_review":
            continue
        _updated = float(getattr(_t, "updated_at", None) or 0)
        if _updated and (now_ts - _updated) < _FORCE_FAIL_WAITING_REVIEW_SECONDS:
            continue
        _verification = dict(getattr(_t, "verification_status", None) or {})
        _strategy = dict(_verification.get("autopilot_strategy") or {})
        _reason_code = str(_strategy.get("reason_code") or "").strip().lower()
        _recover = dict(_verification.get("autopilot_recovery") or {})
        _review_retries = int(_recover.get("waiting_review_retries") or 0)
        _retryable_waiting_review = _reason_code in {
            "proposal_budget_exhausted",
            "autopilot_strategy_exhausted",
            "task_propose_hard_guard",
        }
        if _retryable_waiting_review and _review_retries < _WAITING_REVIEW_RETRY_MAX:
            _recover.update(
                {
                    "waiting_review_retries": _review_retries + 1,
                    "last_waiting_review_retry_at": now_ts,
                    "last_waiting_review_reason_code": _reason_code,
                }
            )
            _verification["autopilot_recovery"] = _recover
            update_local_task_status(
                _t.id,
                "todo",
                verification_status=_verification,
                manual_override_until=now_ts + 20,
                event_type="waiting_for_review_retry_scheduled",
                event_actor="autopilot_tick",
                force=True,
            )
            append_trace_event(
                _t.id,
                "waiting_for_review_retry_scheduled",
                reason_code=_reason_code,
                retry_attempt=_review_retries + 1,
                retry_max=_WAITING_REVIEW_RETRY_MAX,
            )
            continue
        update_local_task_status(
            _t.id,
            "failed",
            error="waiting_for_review_timeout_auto_failed",
            event_type="waiting_for_review_timeout_auto_failed",
            event_actor="autopilot_tick",
            force=True,
        )
        append_trace_event(
            _t.id,
            "waiting_for_review_timeout_auto_failed",
            reason="auto_fail_stale_waiting_for_review",
            timeout_seconds=_FORCE_FAIL_WAITING_REVIEW_SECONDS,
        )

    transitions = services.task_queue_service.reconcile_dependencies(tasks=all_tasks, dependency_resolver=task_dependencies)
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

    dispatch_queue = services.task_queue_service.get_scoped_dispatch_queue(team_id=loop.team_id or None, now=time.time())
    if goal_scope:
        dispatch_queue = [
            item
            for item in dispatch_queue
            if str(getattr(item.get("task"), "goal_id", "") or "").strip() == goal_scope
        ]
    candidates = [item["task"] for item in dispatch_queue if item.get("task") is not None]
    if not candidates:
        # APR-002: autonomous planning recovery — trigger without requiring UI polling
        if goal_scope and not all_tasks:
            repos = get_repository_registry(loop._app)
            _stalled_goal = repos.goal_repo.get_by_id(goal_scope)
            if _stalled_goal and str(getattr(_stalled_goal, "status", "") or "").strip().lower() == "planning":
                from agent.services.lifecycle_service import get_goal_lifecycle_service
                get_goal_lifecycle_service().recover_stalled_planning_goal(_stalled_goal)
        recovered = _maybe_recover_planned_goal_without_candidates(
            loop=loop,
            services=services,
            all_tasks=all_tasks,
            goal_scope=goal_scope,
        )
        _workers_online = services.autopilot_support_service.available_workers(
            team_id=loop.team_id or None,
            is_worker_circuit_open=lambda _url: False,
            app_config=loop._app_config(),
            app=loop._app,
        )[1]
        _no_cand_reason = classify_no_candidate_reason(
            all_tasks=all_tasks,
            workers_available_count=_workers_online,
        )
        loop.last_tick_at = time.time()
        loop.tick_count += 1
        loop._persist_state(enabled=loop.running)
        return {
            "dispatched": 0,
            "reason": "goal_recovery_triggered" if recovered else "no_candidates",
            "no_candidate_reason": _no_cand_reason,
            "debug": build_tick_debug_payload(
                team_id_scope=loop.team_id or None,
                total_tasks_unfiltered=total_tasks_unfiltered,
                total_tasks_scoped=scoped_tasks,
                candidate_count=0,
                workers_online_count=_workers_online,
                workers_available_count=0,
                no_candidate_reason=_no_cand_reason,
            ),
        }

    workers, workers_online_count = services.autopilot_support_service.available_workers(
        team_id=loop.team_id or None,
        is_worker_circuit_open=loop._is_worker_circuit_open,
        app_config=loop._app_config(),
        app=loop._app,
    )
    if not workers:
        loop.last_error = "no_available_workers"
        loop.last_tick_at = time.time()
        loop.tick_count += 1
        loop._persist_state(enabled=loop.running)
        return {
            "dispatched": 0,
            "reason": "no_available_workers",
            "debug": build_tick_debug_payload(
                team_id_scope=loop.team_id or None,
                total_tasks_unfiltered=total_tasks_unfiltered,
                total_tasks_scoped=scoped_tasks,
                candidate_count=len(candidates),
                workers_online_count=workers_online_count,
                workers_available_count=0,
            ),
        }

    dispatched = 0
    completed = 0
    failed = 0
    dispatched_task_ids: list[str] = []
    policy = loop._security_policy()
    fallback_policy = _fallback_policy(loop)
    runtime_caps = _runtime_model_capabilities(loop)
    worker_parallel_cfg = ((loop._agent_config() or {}).get("worker_parallelism") or {}).get("ollama") or {}
    worker_parallelism = max(1, int((worker_parallel_cfg.get("model_defaults") or {}).get("max_parallel_requests") or 1))
    online_worker_capacity = max(1, len(workers)) * worker_parallelism
    runtime_capacity = max(1, int((loop._agent_config() or {}).get("runtime_capacity_cap") or online_worker_capacity))
    ollama_capacity = None
    try:
        parallel_cfg = ((loop._agent_config() or {}).get("worker_parallelism") or {}).get("ollama") or {}
        max_parallel = int((parallel_cfg.get("model_defaults") or {}).get("max_parallel_requests") or 0)
        if max_parallel > 0:
            ollama_capacity = max_parallel
        else:
            ollama_rt = dict((runtime_caps.get("runtime") or {}).get("ollama") or {})
            if ollama_rt.get("ok"):
                ollama_capacity = max(1, int(ollama_rt.get("candidate_count") or 1))
    except Exception:
        ollama_capacity = None
    effective_concurrency = resolve_effective_concurrency(
        requested_max_concurrency=loop.max_concurrency,
        security_policy=policy,
        online_worker_capacity=online_worker_capacity,
        runtime_capacity=runtime_capacity,
        ollama_capacity=ollama_capacity,
    )
    local_worker_url = (settings.agent_url or f"http://localhost:{settings.port}").rstrip("/")
    queue_positions = dispatch_queue_positions(dispatch_queue)

    # thr-010: pre-assign workers sequentially under _routing_lock BEFORE spawning
    # threads so two threads can never receive the same worker slot.
    task_assignments: list[tuple[Any, Any, bool]] = []
    for task in candidates[:effective_concurrency]:
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
        if target_worker is None:
            no_worker_reason = str(assign_reason or "no_worker_available")
            retryable = no_worker_reason in {"assigned_worker_offline", "assigned_worker_is_hub_forbidden", "hub_self_worker_filtered", "no_workers_available"}
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
            continue
        task_assignments.append((task, target_worker, was_assigned))

    # thr-011: propose_timeout + execute_timeout + 30s buffer = hard deadline per task thread.
    per_task_hard_timeout = int(policy.get("propose_timeout", 120)) + int(policy.get("execute_timeout", 60)) + 30
    app = loop._app

    # thr-006: parallel dispatch via ThreadPoolExecutor.
    # thr-015: executor.shutdown(wait=False) so the per-goal tick lock is
    #          released immediately when _stop_event is set. Running threads
    #          continue in the background and update task status on completion.
    # thr-016: per-goal tick tracking (autopilot.py) replaces _tick_lock. Different
    #          goals can tick in parallel; the same goal is guarded by _active_goal_ticks.
    task_results: list[TaskDispatchResult] = []
    dispatch_window_started = time.time()
    # Keep dispatch deterministic and state-safe until async mode is hardened end-to-end.
    async_dispatch_enabled = False
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=max(1, effective_concurrency))
    try:
        for task, _target_worker, _was_assigned in task_assignments:
            try:
                created_at = float(getattr(task, "created_at", 0) or 0)
                if created_at > 0:
                    TASK_QUEUE_WAIT_SECONDS.observe(max(0.0, time.time() - created_at))
            except Exception:
                pass
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

        if async_dispatch_enabled:
            for future, tid in future_to_task_id.items():
                def _done_cb(done_future, task_id=tid):
                    try:
                        done_future.result()
                    except Exception as exc:
                        logging.error("[tick][task_id=%s][async] _dispatch_one_task raised: %s", task_id, exc)
                        update_local_task_status(task_id, "failed", error=str(exc), force=True)
                future.add_done_callback(_done_cb)
            for task, _target_worker, _was_assigned in task_assignments:
                task_results.append(TaskDispatchResult(task_id=task.id, dispatched=True, completed=False, failed=False))
            pending = set()
        else:
            _POLL = 1.0
            pending = set(future_to_task_id.keys())
            timeout_at = time.time() + per_task_hard_timeout
            while pending and time.time() < timeout_at:
                if loop._stop_event.is_set():
                    break
                done, pending = concurrent.futures.wait(
                    pending, timeout=_POLL,
                    return_when=concurrent.futures.FIRST_COMPLETED,
                )
                for future in done:
                    tid = future_to_task_id[future]
                    try:
                        task_results.append(future.result())
                    except Exception as exc:
                        logging.error("[tick][task_id=%s] _dispatch_one_task raised: %s", tid, exc)
                        update_local_task_status(tid, "failed", error=str(exc), force=True)
                        task_results.append(TaskDispatchResult(
                            task_id=tid, failed=True, failure_type="thread_exception"
                        ))

            # Cancel any remaining pending futures (timeout or stop_event).
            for future in pending:
                tid = future_to_task_id[future]
                future.cancel()
                reason = "stop_event" if loop._stop_event.is_set() else f"hard_timeout_{per_task_hard_timeout}s"
                recoverable = reason == "stop_event"
                logging.warning(
                    "[tick][task_id=%s] dispatch aborted (%s), marking %s",
                    tid, reason, "todo" if recoverable else "failed",
                )
                update_local_task_status(
                    tid,
                    "todo" if recoverable else "failed",
                    error=f"dispatch_{reason}",
                    force=True,
                )
                append_trace_event(
                    tid, "dispatch_aborted",
                    reason=reason,
                )
                task_results.append(
                    TaskDispatchResult(
                        task_id=tid,
                        failed=not recoverable,
                        failure_type=("dispatch_aborted" if not recoverable else "recoverable_dispatch_aborted"),
                    )
                )
    finally:
        executor.shutdown(wait=False)
        DISPATCH_WAIT_SECONDS.observe(max(0.0, time.time() - dispatch_window_started))

    # thr-012: Aggregate results into local counters + loop counters (thr-002: via _increment_*).
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
            total_tasks_scoped=scoped_tasks,
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
