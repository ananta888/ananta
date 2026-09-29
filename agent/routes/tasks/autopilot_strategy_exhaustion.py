"""Autopilot strategy exhaustion: model recovery, retry scheduling and task status update.

Runs when no strategy candidate produced an executable proposal. Behavior is
identical to the former inline block of
:func:`agent.routes.tasks.autopilot_task_dispatcher._dispatch_one_task_inner`.
"""

from __future__ import annotations

import time
from typing import Any

from agent.routes.tasks.autopilot_model_selector import _normalize_temperature_value

from .autopilot_dispatch_context import DispatchContext
from .autopilot_proposal_strategies import ProposalStrategyOutcome
from .autopilot_task_dispatcher_helpers import (
    TaskDispatchResult,
    _effective_agent_cfg_for_task,
    _ensure_llm_profile_snapshot,
    _is_terminal_status,
    _merged_last_proposal_snapshot,
    _resolve_autonomous_repair_budget,
    _should_terminalize_no_executable_strategy,
)


def handle_strategy_exhaustion(  # noqa: C901
    ctx: DispatchContext,
    *,
    outcome: ProposalStrategyOutcome,
    strategy_state: dict[str, Any],
    model_meta: dict[str, Any],
    runtime_caps: dict,
) -> TaskDispatchResult:
    """Record the exhausted strategies, try model recovery and schedule the next step."""
    task = ctx.task
    target_worker = ctx.target_worker
    loop = ctx.loop
    app_ctx = ctx.app_ctx
    result = ctx.result
    append_trace_event = ctx.append_trace_event
    update_local_task_status = ctx.update_local_task_status
    current_task_status = ctx.dependencies.current_task_status
    log = ctx.log
    strategy_failures = outcome.strategy_failures
    collected_llm_profiles = outcome.collected_llm_profiles
    strategy_cfg = outcome.strategy_cfg
    rejected_terminal_model_signal_seen = outcome.rejected_terminal_model_signal_seen
    verified_terminal_model_signal_seen = outcome.verified_terminal_model_signal_seen
    latest_status = current_task_status(task.id, app=app_ctx)
    if _is_terminal_status(latest_status):
        append_trace_event(
            task.id,
            "autopilot_strategy_exhausted_skipped_terminal",
            delegated_to=target_worker.url,
            terminal_status=latest_status,
        )
        result.dispatched = True
        result.completed = latest_status == "completed"
        result.failed = latest_status != "completed"
        result.failure_type = None if result.completed else latest_status
        return result
    now_ts = time.time()
    total_attempt_count = int(strategy_state.get("attempt_count") or 0) + len(strategy_failures)
    failed_models = list(strategy_state.get("failed_models") or [])
    failed_temperatures = list(strategy_state.get("failed_temperatures") or [])
    failed_sources = list(strategy_state.get("failed_sources") or [])
    for item in strategy_failures:
        model = str(item.get("model") or "").strip()
        if model and model not in failed_models:
            failed_models.append(model)
        temperature = _normalize_temperature_value(item.get("temperature"))
        if temperature is not None and temperature not in failed_temperatures:
            failed_temperatures.append(temperature)
        source = str(item.get("source") or "").strip()
        if source and source not in failed_sources:
            failed_sources.append(source)
    terminalize_no_exec = _should_terminalize_no_executable_strategy(strategy_failures)
    cooldown_seconds = 0
    reason_code = (
        "autopilot_strategy_invalid_proposal_terminal"
        if terminalize_no_exec
        else "autopilot_strategy_exhausted"
    )
    existing_strategy_state = dict(strategy_state or {})
    repair_rounds = int(existing_strategy_state.get("repair_rounds") or 0)
    agent_cfg = _effective_agent_cfg_for_task(loop=loop, task=task)
    propose_policy_cfg = dict((agent_cfg.get("propose_policy") or {}))
    allow_human_review = bool(propose_policy_cfg.get("allow_human_review", True))
    on_declined = str(propose_policy_cfg.get("on_all_strategies_declined") or "needs_review").strip().lower()
    repair_budget, repair_delay_seconds = _resolve_autonomous_repair_budget(agent_cfg=agent_cfg)
    recovery_outcome: dict[str, Any] = {}
    try:
        from agent.services.model_recovery_strategy_executor import (
            get_model_recovery_strategy_executor,
        )

        recovery_outcome = (
            get_model_recovery_strategy_executor().execute_after_model_exhaustion(
                task=task,
                strategy_failures=strategy_failures,
            )
            or {}
        )
    except Exception as recovery_exc:
        log.warning(
            "Hub task recovery proposal failed for task %s: %s",
            task.id,
            recovery_exc,
        )
        recovery_outcome = {
            "status": "failed",
            "reason_code": "task_recovery_coordinator_failed",
            "error_type": type(recovery_exc).__name__,
        }
    recovery_pending = (
        str(recovery_outcome.get("status") or "").strip().lower()
        == "pending_approval"
    )
    recovery_terminal_handled = bool(
        recovery_outcome.get(
            "terminal_model_chain_handled"
        )
    ) or verified_terminal_model_signal_seen
    schedule_repair_retry = (
        terminalize_no_exec
        and not allow_human_review
        and repair_rounds < repair_budget
        and not recovery_pending
        and not recovery_terminal_handled
        and not rejected_terminal_model_signal_seen
    )
    if recovery_pending:
        retry_status = "waiting_for_review"
        cooldown_seconds = 0
        reason_code = "model_recovery_plan_pending_approval"
    elif rejected_terminal_model_signal_seen:
        retry_status = (
            "waiting_for_review"
            if allow_human_review
            else "failed"
        )
        cooldown_seconds = 0
        reason_code = (
            "invalid_terminal_model_recovery_signal"
        )
    elif recovery_terminal_handled:
        retry_status = (
            "waiting_for_review"
            if allow_human_review
            else "failed"
        )
        cooldown_seconds = 0
        reason_code = str(
            recovery_outcome.get("reason_code")
            or "model_recovery_stopped"
        )[:160]
    elif schedule_repair_retry:
        retry_status = "todo"
        cooldown_seconds = repair_delay_seconds
    elif terminalize_no_exec and allow_human_review:
        retry_status = "todo"
        cooldown_seconds = int(strategy_cfg.get("cooldown_seconds") or 20)
    elif on_declined == "failed":
        retry_status = "failed"
    elif on_declined == "advisory":
        retry_status = "todo"
    else:
        retry_status = "waiting_for_review" if allow_human_review else "failed"
    verification_status = {
        **dict(getattr(task, "verification_status", None) or {}),
        "autopilot_strategy": {
            "attempt_count": total_attempt_count,
            "repair_rounds": repair_rounds + (1 if schedule_repair_retry else 0),
            "failed_models": failed_models,
            "failed_temperatures": failed_temperatures,
            "failed_sources": failed_sources,
            "runtime": dict(runtime_caps.get("runtime") or {}),
            "last_failures": strategy_failures[-5:],
            "last_failed_at": now_ts,
            "next_retry_after": (now_ts + cooldown_seconds) if cooldown_seconds > 0 else now_ts,
            "reason_code": reason_code,
        },
        **(
            {
                "model_recovery": {
                    "schema": "ananta.task_recovery_state.v1",
                    "status": "pending_approval",
                    "plan_id": recovery_outcome.get("plan_id"),
                    "approval_request_id": recovery_outcome.get("approval_request_id"),
                    "recovery_key": recovery_outcome.get("recovery_key"),
                    "node_count": recovery_outcome.get("node_count"),
                }
            }
            if recovery_pending
            else {}
        ),
        **(
            {
                "model_recovery_strategy": {
                    "schema": (
                        "ananta.model_recovery_strategy.v1"
                    ),
                    "status": recovery_outcome.get(
                        "status"
                    ),
                    "reason_code": recovery_outcome.get(
                        "reason_code"
                    ),
                    "recovery_actions": list(
                        recovery_outcome.get(
                            "recovery_actions"
                        )
                        or []
                    ),
                    "policy_hash": recovery_outcome.get(
                        "policy_hash"
                    ),
                    "compaction": dict(
                        recovery_outcome.get(
                            "compaction"
                        )
                        or {}
                    ),
                    "compacted_context_hash": (
                        recovery_outcome.get(
                            "compacted_context_hash"
                        )
                    ),
                }
            }
            if recovery_terminal_handled
            else {}
        ),
    }
    retry_snapshot = _ensure_llm_profile_snapshot(
        snapshot={"strategy_failures": strategy_failures[-5:]},
        strategy_id=None,
        model_meta=model_meta if isinstance(model_meta, dict) else None,
        preferred_profile=collected_llm_profiles,
        allow_synthetic_fallback=bool(
            ((loop._agent_config() or {}).get("llm_profile_policy") or {}).get(
                "allow_synthetic_fallback", False
            )
        ),
    )
    if recovery_pending:
        authoritative_task = (
            ctx.dependencies.repository_registry(app_ctx)
            .task_repo.get_by_id(task.id)
        )
        authoritative_recovery = dict(
            (
                getattr(
                    authoritative_task,
                    "status_reason_details",
                    None,
                )
                or {}
            ).get("model_recovery")
            or {}
        )
        authoritative_recovery_status = str(
            authoritative_recovery.get("status") or ""
        ).strip().lower()
        if authoritative_recovery_status not in {
            "",
            "pending_approval",
        }:
            append_trace_event(
                task.id,
                "autopilot_recovery_state_write_skipped",
                delegated_to=target_worker.url,
                authoritative_recovery_status=(
                    authoritative_recovery_status
                ),
                recovery_plan_id=(
                    authoritative_recovery.get("plan_id")
                ),
            )
            result.dispatched = True
            result.failed = (
                authoritative_recovery_status
                not in {
                    "materialized",
                    "materialized_waiting_for_children",
                }
            )
            result.failure_type = (
                None
                if not result.failed
                else authoritative_recovery_status
            )
            return result
    latest_status = current_task_status(task.id, app=app_ctx)
    if _is_terminal_status(latest_status):
        append_trace_event(
            task.id,
            "autopilot_strategy_update_skipped_terminal",
            delegated_to=target_worker.url,
            terminal_status=latest_status,
            recovery_plan_id=recovery_outcome.get("plan_id"),
        )
        result.dispatched = True
        result.completed = latest_status == "completed"
        result.failed = latest_status != "completed"
        result.failure_type = (
            None if result.completed else latest_status
        )
        return result
    update_local_task_status(
        task.id,
        retry_status,
        error="autopilot_strategy_exhausted",
        verification_status=verification_status,
        status_reason_code=reason_code,
        status_reason_details={
            **dict(getattr(task, "status_reason_details", None) or {}),
            **(
                {
                    "model_recovery": {
                        "schema": "ananta.task_recovery_state.v1",
                        "status": "pending_approval",
                        "plan_id": recovery_outcome.get("plan_id"),
                        "approval_request_id": recovery_outcome.get("approval_request_id"),
                        "recovery_key": recovery_outcome.get("recovery_key"),
                        "recovery_depth": 1,
                    }
                }
                if recovery_pending
                else {}
            ),
            **(
                {
                    "model_recovery_strategy": {
                        "schema": (
                            "ananta.model_recovery_strategy.v1"
                        ),
                        "status": recovery_outcome.get(
                            "status"
                        ),
                        "reason_code": (
                            recovery_outcome.get(
                                "reason_code"
                            )
                        ),
                        "recovery_actions": list(
                            recovery_outcome.get(
                                "recovery_actions"
                            )
                            or []
                        ),
                        "policy_hash": (
                            recovery_outcome.get(
                                "policy_hash"
                            )
                        ),
                        "compaction": dict(
                            recovery_outcome.get(
                                "compaction"
                            )
                            or {}
                        ),
                        "compacted_context_hash": (
                            recovery_outcome.get(
                                "compacted_context_hash"
                            )
                        ),
                    }
                }
                if recovery_terminal_handled
                else {}
            ),
        },
        manual_override_until=(now_ts + cooldown_seconds) if cooldown_seconds > 0 else None,
        last_proposal=_merged_last_proposal_snapshot(
            task_id=task.id,
            snapshot=retry_snapshot,
            app=app_ctx,
        ),
        force=False,
        event_type=(
            "task_recovery_plan_pending_approval"
            if recovery_pending
            else (
                "model_recovery_strategy_stopped"
                if recovery_terminal_handled
                else (
                    "invalid_terminal_model_recovery_signal_stopped"
                    if rejected_terminal_model_signal_seen
                    else "autopilot_strategy_retry_scheduled"
                )
            )
        ),
        event_actor="autopilot_tick",
        event_details={
            "retry_status": retry_status,
            "cooldown_seconds": cooldown_seconds,
            "attempt_count": total_attempt_count,
            "terminalize_no_exec": terminalize_no_exec,
            "schedule_repair_retry": schedule_repair_retry,
            "repair_rounds": repair_rounds + (1 if schedule_repair_retry else 0),
            "repair_budget": repair_budget,
            "allow_human_review": allow_human_review,
            "on_all_strategies_declined": on_declined,
            "model_recovery_status": recovery_outcome.get("status"),
            "recovery_plan_id": recovery_outcome.get("plan_id"),
            "approval_request_id": recovery_outcome.get("approval_request_id"),
        },
    )
    append_trace_event(
        task.id,
        "autopilot_strategy_exhausted",
        delegated_to=target_worker.url,
        failures=strategy_failures[-5:],
        cooldown_seconds=cooldown_seconds,
        model_recovery={
            "status": recovery_outcome.get("status"),
            "reason_code": recovery_outcome.get("reason_code"),
            "plan_id": recovery_outcome.get("plan_id"),
            "approval_request_id": recovery_outcome.get("approval_request_id"),
        },
    )
    result.failed = True
    result.failure_type = (
        "recovery_plan_pending_approval"
        if recovery_pending
        else (
            str(recovery_outcome.get("reason_code"))
            if recovery_terminal_handled
            else "strategy_exhausted"
        )
    )
    return result
