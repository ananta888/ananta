from __future__ import annotations

import logging
import time
from typing import Any, Callable

from agent.config import settings
from agent.routes.tasks.autopilot_dispatch_policy import (
    build_tick_debug_payload,
    dispatch_queue_positions,
    resolve_effective_concurrency,
    resolve_target_worker_for_task,
)


def _fallback_policy(loop: Any) -> dict[str, Any]:
    cfg = (loop._agent_config() or {}).get("execution_fallback_policy", {}) or {}
    return {
        "allow_hub_worker_fallback": bool(cfg.get("allow_hub_worker_fallback", True)),
        "escalate_on_fallback_block": bool(cfg.get("escalate_on_fallback_block", True)),
        "fallback_block_status": str(cfg.get("fallback_block_status") or "blocked").strip().lower() or "blocked",
    }


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

    total_tasks_unfiltered = len(services.autopilot_support_service.scoped_tasks(team_id=None, app=loop._app))
    all_tasks = services.autopilot_support_service.scoped_tasks(team_id=loop.team_id or None, app=loop._app)
    scoped_tasks = len(all_tasks)
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
    candidates = [item["task"] for item in dispatch_queue if item.get("task") is not None]
    if not candidates:
        loop.last_tick_at = time.time()
        loop.tick_count += 1
        loop._persist_state(enabled=loop.running)
        return {
            "dispatched": 0,
            "reason": "no_candidates",
            "debug": build_tick_debug_payload(
                team_id_scope=loop.team_id or None,
                total_tasks_unfiltered=total_tasks_unfiltered,
                total_tasks_scoped=scoped_tasks,
                candidate_count=0,
                workers_online_count=services.autopilot_support_service.available_workers(
                    team_id=loop.team_id or None,
                    is_worker_circuit_open=lambda _url: False,
                    app_config=loop._app_config(),
                    app=loop._app,
                )[1],
                workers_available_count=0,
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
    policy = loop._security_policy()
    fallback_policy = _fallback_policy(loop)
    effective_concurrency = resolve_effective_concurrency(
        requested_max_concurrency=loop.max_concurrency,
        security_policy=policy,
    )
    local_worker_url = (settings.agent_url or f"http://localhost:{settings.port}").rstrip("/")
    queue_positions = dispatch_queue_positions(dispatch_queue)
    for task in candidates[:effective_concurrency]:
        target_worker, loop._worker_cursor, was_assigned = resolve_target_worker_for_task(
            task=task,
            workers=workers,
            worker_cursor=loop._worker_cursor,
        )
        if was_assigned:
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
        is_local_fallback = settings.role == "hub" and settings.hub_can_be_worker and target_worker.url.rstrip("/") == local_worker_url
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
            loop.failed_count += 1
            continue

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
        update_local_task_status(
            task.id,
            str(getattr(task, "status", None) or "assigned"),
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
            },
        )

        try:
            propose_data = loop._forward_with_retry(
                target_worker.url,
                f"/tasks/{task.id}/step/propose",
                {"task_id": task.id},
                token=target_worker.token,
            )
        except Exception as e:
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
            if loop._is_worker_circuit_open(target_worker.url):
                append_trace_event(
                    task.id,
                    "autopilot_worker_circuit_open",
                    worker_url=target_worker.url,
                    reason="forward_failed",
                    open_until=loop._worker_circuit_open_until.get(target_worker.url),
                    failure_streak=int(loop._worker_failure_streak.get(target_worker.url, 0)),
                )
            loop.failed_count += 1
            continue
        command = propose_data.get("command")
        tool_calls = propose_data.get("tool_calls")
        reason = propose_data.get("reason")
        proposal_snapshot = services.autopilot_decision_service.build_proposal_snapshot(propose_data)
        raw_preview = proposal_snapshot.get("raw_preview")
        if not command and not tool_calls:
            update_local_task_status(
                task.id,
                "failed",
                error="autopilot_no_executable_step",
                last_proposal=proposal_snapshot,
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
            loop.failed_count += 1
            continue

        append_trace_event(
            task.id,
            "autopilot_decision",
            delegated_to=target_worker.url,
            reason=reason,
            command=command,
            tool_calls=tool_calls,
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
                    last_proposal=proposal_snapshot,
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
                loop.failed_count += 1
                continue

        execute_payload = {
            "task_id": task.id,
            "command": command,
            "tool_calls": tool_calls,
            "timeout": int(policy["execute_timeout"]),
            "retries": int(policy["execute_retries"]),
        }
        try:
            execute_data = loop._forward_with_retry(
                target_worker.url,
                f"/tasks/{task.id}/step/execute",
                execute_payload,
                token=target_worker.token,
            )
        except Exception as e:
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
            if loop._is_worker_circuit_open(target_worker.url):
                append_trace_event(
                    task.id,
                    "autopilot_worker_circuit_open",
                    worker_url=target_worker.url,
                    reason="forward_failed",
                    open_until=loop._worker_circuit_open_until.get(target_worker.url),
                    failure_streak=int(loop._worker_failure_streak.get(target_worker.url, 0)),
                )
            loop.failed_count += 1
            continue
        task_status, exit_code, output = services.autopilot_decision_service.normalize_execute_result(execute_data)
        task_status, output, quality_gate_reason = services.autopilot_decision_service.apply_quality_gate_if_needed(
            task=task,
            task_status=task_status,
            output=output,
            exit_code=exit_code,
            agent_cfg=loop._agent_config(),
        )
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
            last_proposal=proposal_snapshot,
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
        loop.dispatched_count += 1
        dispatched += 1
        if task_status == "completed":
            loop.completed_count += 1
            try:
                from agent.routes.tasks.auto_planner import auto_planner

                if auto_planner.auto_followup_enabled:
                    auto_planner.analyze_and_create_followups(
                        task_id=task.id,
                        output=output,
                        exit_code=exit_code,
                    )
            except Exception as e:
                logging.debug(f"Followup analysis skipped for {task.id}: {e}")
        else:
            loop.failed_count += 1

    loop.last_tick_at = time.time()
    loop.last_error = None
    loop.tick_count += 1
    loop._persist_state(enabled=loop.running)
    return {
        "dispatched": dispatched,
        "reason": "ok",
        "debug": build_tick_debug_payload(
            team_id_scope=loop.team_id or None,
            total_tasks_unfiltered=total_tasks_unfiltered,
            total_tasks_scoped=scoped_tasks,
            candidate_count=len(candidates),
            workers_online_count=workers_online_count,
            workers_available_count=len(workers),
        ),
    }
