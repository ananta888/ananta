"""Autopilot proposal phase: propose guards and the per-candidate strategy loop.

:func:`run_proposal_strategies` asks the worker for a step proposal with each
strategy candidate until one yields an executable step, the proposal budget is
exhausted or a terminal model-chain signal is observed. Behavior is identical to
the former inline block of
:func:`agent.routes.tasks.autopilot_task_dispatcher._dispatch_one_task_inner`.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any

from agent.metrics import (
    STRATEGY_ATTEMPT_COUNT,
    WORKER_PROPOSE_DURATION_SECONDS,
)
from agent.routes.tasks.autopilot_model_selector import _normalize_temperature_value
from agent.routes.tasks.autopilot_strategy_candidates import (
    _safe_context_length,
    _strategy_cfg,
)
from agent.services.recovery_dispatch_gate_service import (
    recovery_dispatch_request_fingerprint,
)
from agent.tool_guardrails import estimate_text_tokens
from ananta_contracts.model_recovery import (
    is_recoverable_model_error_type,
    sanitize_terminal_model_recovery_signal,
)

from .autopilot_dispatch_context import DispatchContext
from .autopilot_task_dispatcher_helpers import (
    TaskDispatchResult,
    _is_terminal_status,
    _recent_strategy_attempts,
)


@dataclass
class ProposalStrategyOutcome:
    """What the strategy loop learned; ``stop_result`` ends the dispatch early."""

    stop_result: TaskDispatchResult | None = None
    propose_data: Any = None
    strategy_cfg: dict[str, Any] = field(default_factory=dict)
    strategy_failures: list[dict[str, Any]] = field(default_factory=list)
    collected_llm_profiles: list[dict[str, Any]] = field(default_factory=list)
    selected_attempt_meta: dict[str, Any] = field(default_factory=dict)
    rejected_terminal_model_signal_seen: bool = False
    verified_terminal_model_signal_seen: bool = False

    @classmethod
    def stopped(cls, result: TaskDispatchResult) -> "ProposalStrategyOutcome":
        return cls(stop_result=result)


def _defer_for_provider_backpressure(ctx: DispatchContext) -> TaskDispatchResult | None:
    """Defer the task while the local LLM provider signals backpressure."""
    task = ctx.task
    target_worker = ctx.target_worker
    loop = ctx.loop
    result = ctx.result
    append_trace_event = ctx.append_trace_event
    update_local_task_status = ctx.update_local_task_status
    is_backpressure_active = getattr(loop, "_is_provider_backpressure_active", None)
    backpressure_details = getattr(loop, "_provider_backpressure_details", None)
    if (
        not os.environ.get("PYTEST_CURRENT_TEST")
        and callable(is_backpressure_active)
        and is_backpressure_active("ollama")
    ):
        hold_until, hold_reason = (backpressure_details("ollama") if callable(backpressure_details) else (0.0, ""))
        hold_for = max(1, int(hold_until - time.time()))
        append_trace_event(
            task.id,
            "autopilot_provider_backpressure_deferred",
            delegated_to=target_worker.url,
            provider="ollama",
            hold_seconds=hold_for,
            reason=hold_reason or "ollama_generate_timeout",
        )
        update_local_task_status(
            task.id,
            str(getattr(task, "status", None) or "assigned"),
            manual_override_until=hold_until if hold_until > 0 else None,
            verification_status={
                **dict(getattr(task, "verification_status", None) or {}),
                "provider_backpressure": {
                    "provider": "ollama",
                    "reason": hold_reason or "ollama_generate_timeout",
                    "deferred_until": hold_until,
                    "deferred_at": time.time(),
                },
            },
        )
        return result
    return None


def _trip_propose_hard_guard(ctx: DispatchContext) -> TaskDispatchResult | None:
    """Stop tasks that exceeded the propose-attempt hard guard within its window."""
    task = ctx.task
    target_worker = ctx.target_worker
    loop = ctx.loop
    result = ctx.result
    append_trace_event = ctx.append_trace_event
    update_local_task_status = ctx.update_local_task_status
    hard_guard_max_attempts_window = max(
        5,
        min(int((loop._agent_config() or {}).get("autopilot_task_propose_hard_guard_max_attempts") or 30), 500),
    )
    hard_guard_window_seconds = max(
        10,
        min(int((loop._agent_config() or {}).get("autopilot_task_propose_hard_guard_window_seconds") or 180), 3600),
    )
    hard_guard_status = str(
        (loop._agent_config() or {}).get("autopilot_task_propose_hard_guard_status") or "needs_review"
    ).strip().lower()
    if hard_guard_status not in {"needs_review", "failed", "todo"}:
        hard_guard_status = "needs_review"
    recent_attempts = _recent_strategy_attempts(task, now_ts=time.time(), window_seconds=hard_guard_window_seconds)
    if recent_attempts >= hard_guard_max_attempts_window:
        _verification = {
            **dict(getattr(task, "verification_status", None) or {}),
            "autopilot_strategy": {
                **dict((getattr(task, "verification_status", None) or {}).get("autopilot_strategy") or {}),
                "reason_code": "task_propose_hard_guard",
                "last_failed_at": time.time(),
            },
        }
        update_local_task_status(
            task.id,
            hard_guard_status,
            error="autopilot_task_propose_hard_guard_triggered",
            verification_status=_verification,
            force=True,
            event_type="autopilot_task_propose_hard_guard_triggered",
            event_actor="autopilot_tick",
            event_details={
                "recent_attempts": int(recent_attempts),
                "window_seconds": int(hard_guard_window_seconds),
                "max_attempts": int(hard_guard_max_attempts_window),
                "status": hard_guard_status,
            },
        )
        append_trace_event(
            task.id,
            "autopilot_task_propose_hard_guard_triggered",
            delegated_to=target_worker.url,
            recent_attempts=recent_attempts,
            window_seconds=hard_guard_window_seconds,
            max_attempts=hard_guard_max_attempts_window,
            status=hard_guard_status,
        )
        result.failed = True
        result.failure_type = "task_propose_hard_guard"
        return result
    return None


def _skip_inflight_propose(ctx: DispatchContext) -> TaskDispatchResult | None:
    """Skip a task whose propose is still in flight within the cooldown."""
    task = ctx.task
    target_worker = ctx.target_worker
    loop = ctx.loop
    result = ctx.result
    append_trace_event = ctx.append_trace_event
    # Prevent duplicate rapid-fire propose dispatches while task is already in-flight.
    propose_inflight_cooldown_s = max(
        10.0,
        float(
            ((loop._agent_config() or {}).get("autopilot", {}) or {})
            .get("strategy", {})
            .get("propose_inflight_cooldown_seconds", 45.0)
        ),
    )
    task_status_now = str(getattr(task, "status", "") or "").strip().lower()
    task_updated_at = float(getattr(task, "updated_at", 0.0) or 0.0)
    task_age_s = max(0.0, time.time() - task_updated_at) if task_updated_at else None
    if task_status_now == "proposing" and task_age_s is not None and task_age_s < propose_inflight_cooldown_s:
        append_trace_event(
            task.id,
            "autopilot_propose_cooldown_skip",
            delegated_to=target_worker.url,
            status=task_status_now,
            updated_age_seconds=round(task_age_s, 3),
            cooldown_seconds=propose_inflight_cooldown_s,
        )
        result.dispatched = True
        result.failed = False
        result.completed = False
        result.failure_type = None
        return result
    return None


def _proposal_budget_failure(
    attempt_index: int,
    *,
    elapsed: float,
    max_total_seconds: int,
    max_llm_calls: int,
    max_strategy_attempts: int,
) -> dict[str, Any] | None:
    """The strategy-failure record when the proposal budget is exhausted, else ``None``."""
    if elapsed > max_total_seconds:
        return {
            "attempt": attempt_index,
            "reason": "proposal_budget_exhausted_total_seconds",
            "elapsed_seconds": round(elapsed, 3),
            "max_total_seconds": max_total_seconds,
            "failure_type": "proposal_budget_exhausted",
        }
    if (attempt_index - 1) >= max_llm_calls or (attempt_index - 1) >= max_strategy_attempts:
        return {
            "attempt": attempt_index,
            "reason": "proposal_budget_exhausted_llm_calls",
            "max_llm_calls": max_llm_calls,
            "max_strategy_attempts": max_strategy_attempts,
            "failure_type": "proposal_budget_exhausted",
        }
    return None


def _base_propose_payload(task_id: str, loop: Any, context_window: Any) -> dict[str, Any]:
    propose_payload: dict[str, Any] = {"task_id": task_id, "context_window": context_window(loop)}
    autopilot_cfg = ((loop._agent_config() or {}).get("autopilot", {}) or {})
    strategy_mode_override = str(
        autopilot_cfg.get("strategy_mode_override") or "autopilot_no_human_review"
    ).strip().lower()
    if strategy_mode_override:
        propose_payload["strategy_mode"] = strategy_mode_override
    return propose_payload


def run_proposal_strategies(
    ctx: DispatchContext,
    *,
    strategy_candidates: list[dict[str, Any]],
    runtime_caps: dict,
    context_window: Any,
) -> ProposalStrategyOutcome:
    """Run the proposal guards, then try each strategy candidate against the worker."""
    task = ctx.task
    target_worker = ctx.target_worker
    loop = ctx.loop
    services = ctx.services
    app_ctx = ctx.app_ctx
    result = ctx.result
    recovery_gate = ctx.recovery_gate
    append_trace_event = ctx.append_trace_event
    update_local_task_status = ctx.update_local_task_status
    current_task_status = ctx.dependencies.current_task_status
    propose_data = None
    strategy_failures: list[dict[str, Any]] = []
    collected_llm_profiles: list[dict[str, Any]] = []
    selected_attempt_meta: dict[str, Any] = {}
    rejected_terminal_model_signal_seen = False
    verified_terminal_model_signal_seen = False
    required_context_tokens = max(
        1024,
        estimate_text_tokens(getattr(task, "title", None))
        + estimate_text_tokens(getattr(task, "description", None))
        + 768,
    )
    strategy_cfg = _strategy_cfg(loop)
    stop_result = _defer_for_provider_backpressure(ctx)
    if stop_result is not None:
        return ProposalStrategyOutcome.stopped(stop_result)
    budget = dict(strategy_cfg.get("proposal_budget") or {})
    budget_started_at = time.time()
    max_total_seconds = int(budget.get("max_total_seconds") or 90)
    max_llm_calls = int(budget.get("max_llm_calls") or 2)
    max_strategy_attempts = int(budget.get("max_strategy_attempts") or 2)
    stop_result = _trip_propose_hard_guard(ctx)
    if stop_result is not None:
        return ProposalStrategyOutcome.stopped(stop_result)
    stop_result = _skip_inflight_propose(ctx)
    if stop_result is not None:
        return ProposalStrategyOutcome.stopped(stop_result)
    STRATEGY_ATTEMPT_COUNT.observe(float(len(strategy_candidates)))
    for attempt_index, candidate in enumerate(strategy_candidates, start=1):
        # Hard guard: never re-propose terminal tasks, even inside strategy loops.
        latest_status = current_task_status(task.id, app=app_ctx)
        if _is_terminal_status(latest_status):
            append_trace_event(
                task.id,
                "autopilot_strategy_attempt_skipped_terminal",
                delegated_to=target_worker.url,
                terminal_status=latest_status,
                attempt=attempt_index,
            )
            result.dispatched = True
            result.completed = latest_status == "completed"
            result.failed = latest_status != "completed"
            result.failure_type = None if result.completed else latest_status
            return ProposalStrategyOutcome.stopped(result)
        budget_failure = _proposal_budget_failure(
            attempt_index,
            elapsed=time.time() - budget_started_at,
            max_total_seconds=max_total_seconds,
            max_llm_calls=max_llm_calls,
            max_strategy_attempts=max_strategy_attempts,
        )
        if budget_failure is not None:
            strategy_failures.append(budget_failure)
            break
        propose_payload = _base_propose_payload(task.id, loop, context_window)
        candidate_model = candidate.get("model")
        candidate_source = str(candidate.get("source") or "strategy")
        candidate_temperature = _normalize_temperature_value(candidate.get("temperature"))
        runtime_model_caps = dict((runtime_caps.get("models") or {}).get(str(candidate_model or "").strip()) or {})
        model_context_length = _safe_context_length(runtime_model_caps.get("context_length"))
        runtime_provider = str(runtime_model_caps.get("provider") or "") or None
        if model_context_length is not None and model_context_length < required_context_tokens:
            strategy_failures.append(
                {
                    "attempt": attempt_index,
                    "model": candidate_model,
                    "temperature": candidate_temperature,
                    "source": candidate_source,
                    "reason": "insufficient_context_window",
                    "required_context_tokens": required_context_tokens,
                    "model_context_length": model_context_length,
                    "runtime_provider": runtime_provider,
                    "failure_type": "preflight_context_limit",
                }
            )
            append_trace_event(
                task.id,
                "autopilot_strategy_attempt_skipped",
                delegated_to=target_worker.url,
                attempt=attempt_index,
                model=candidate_model,
                temperature=candidate_temperature,
                reason="insufficient_context_window",
                required_context_tokens=required_context_tokens,
                model_context_length=model_context_length,
                runtime_provider=runtime_provider,
            )
            continue
        if candidate_model:
            propose_payload["model"] = candidate_model
        if candidate_temperature is not None:
            propose_payload["temperature"] = candidate_temperature
        from agent.services.recovery_task_mutation_policy import (
            recovery_task_role,
        )

        run_evidence_context = None
        if recovery_task_role(task) == "child":
            run_evidence_context = (
                recovery_gate.reserve_run_evidence_context(
                    task.id,
                    worker_url=target_worker.url,
                    replace=True,
                    app=app_ctx,
                )
            )
        if run_evidence_context is not None:
            propose_payload[
                "recovery_run_evidence_context"
            ] = run_evidence_context
        dispatch_lease = recovery_gate.acquire_dispatch_lease(
            task.id,
            phase="propose",
            worker_url=target_worker.url,
            request_fingerprint=(
                recovery_dispatch_request_fingerprint(
                    "propose",
                    propose_payload,
                )
            ),
            app=app_ctx,
        )
        if not dispatch_lease.allowed:
            append_trace_event(
                task.id,
                "autopilot_propose_skipped_recovery_lease",
                delegated_to=target_worker.url,
                reason_code=(
                    dispatch_lease.decision.reason_code
                ),
                source_task_id=(
                    dispatch_lease.decision.source_task_id
                ),
                plan_id=dispatch_lease.decision.plan_id,
            )
            result.dispatched = True
            result.failed = True
            result.failure_type = (
                dispatch_lease.decision.reason_code
            )
            return ProposalStrategyOutcome.stopped(result)
        if dispatch_lease.token:
            propose_payload["dispatch_lease_token"] = (
                dispatch_lease.token
            )
            propose_payload["dispatch_lease_phase"] = "propose"
        append_trace_event(
            task.id,
            "autopilot_strategy_attempt",
            delegated_to=target_worker.url,
            attempt=attempt_index,
            model=candidate_model,
            temperature=candidate_temperature,
            runtime_provider=runtime_provider,
            model_context_length=model_context_length,
            required_context_tokens=required_context_tokens,
            source=candidate_source,
        )
        # Guard against stale dispatch candidates: task may have reached a
        # terminal status while this strategy attempt was prepared.
        latest_before_propose = current_task_status(task.id, app=getattr(loop, "_app", app_ctx) or app_ctx)
        if _is_terminal_status(latest_before_propose):
            append_trace_event(
                task.id,
                "autopilot_strategy_attempt_skipped",
                delegated_to=target_worker.url,
                attempt=attempt_index,
                model=candidate_model,
                temperature=candidate_temperature,
                reason="task_already_terminal_before_propose",
                latest_status=latest_before_propose,
                runtime_provider=runtime_provider,
            )
            continue
        # Mark propose-in-flight to avoid duplicate concurrent dispatches
        # on the same task while the worker is evaluating the proposal.
        update_local_task_status(
            task.id,
            "proposing",
            assigned_agent_url=target_worker.url,
            assigned_agent_token=target_worker.token,
            event_type="autopilot_propose_started",
            event_actor="autopilot_tick",
            force=True,
        )
        try:
            _propose_started = time.time()
            candidate_data = loop._forward_with_retry(
                target_worker.url,
                f"/tasks/{task.id}/step/propose",
                propose_payload,
                token=target_worker.token,
            )
            WORKER_PROPOSE_DURATION_SECONDS.observe(max(0.0, time.time() - _propose_started))
            if dispatch_lease.token:
                with recovery_gate.result_guard(
                    task.id,
                    token=dispatch_lease.token,
                    phase="propose",
                    request_fingerprint=(
                        recovery_dispatch_request_fingerprint(
                            "propose",
                            propose_payload,
                        )
                    ),
                    worker_url=target_worker.url,
                    app=app_ctx,
                ) as result_decision:
                    if not result_decision.allowed:
                        append_trace_event(
                            task.id,
                            "autopilot_propose_result_rejected",
                            delegated_to=target_worker.url,
                            reason_code=(
                                result_decision.reason_code
                            ),
                        )
                        result.dispatched = True
                        result.failed = True
                        result.failure_type = (
                            result_decision.reason_code
                        )
                        return ProposalStrategyOutcome.stopped(result)
                    candidate_data = (
                        services.autopilot_decision_service
                        .normalize_proposal_data(candidate_data)
                    )
                    from agent.services.recovery_worker_result_service import (
                        get_recovery_worker_result_service,
                    )

                    get_recovery_worker_result_service().accept_proposal_response(
                        task_id=task.id,
                        response=candidate_data,
                    )
            else:
                candidate_data = services.autopilot_decision_service.normalize_proposal_data(candidate_data)
        except Exception as strategy_exc:
            if dispatch_lease.token:
                recovery_gate.revoke_dispatch_lease(
                    task.id,
                    reason_code=(
                        "recovery_dispatch_transport_outcome_unknown"
                    ),
                    app=app_ctx,
                )
                append_trace_event(
                    task.id,
                    "autopilot_propose_transport_fenced",
                    delegated_to=target_worker.url,
                    attempt=attempt_index,
                    reason=str(strategy_exc),
                    reason_code=(
                        "recovery_dispatch_transport_outcome_unknown"
                    ),
                )
            record_propose_attempt = getattr(loop, "_record_task_propose_attempt", None)
            if callable(record_propose_attempt):
                record_propose_attempt(task.id, success=False)
            strategy_failures.append(
                {
                    "attempt": attempt_index,
                    "model": candidate_model,
                    "temperature": candidate_temperature,
                    "source": candidate_source,
                    "reason": str(strategy_exc),
                    "runtime_provider": runtime_provider,
                    "model_context_length": model_context_length,
                    "required_context_tokens": required_context_tokens,
                    "failure_type": "forward_error",
                }
            )
            continue
        candidate_snapshot = services.autopilot_decision_service.build_proposal_snapshot(candidate_data)
        candidate_cli = candidate_snapshot.get("cli_result")
        if isinstance(candidate_cli, dict):
            for entry in list(candidate_cli.get("llm_call_profile") or []):
                if isinstance(entry, dict):
                    collected_llm_profiles.append(dict(entry))
        if candidate_snapshot.get("command") or candidate_snapshot.get("tool_calls"):
            record_propose_attempt = getattr(loop, "_record_task_propose_attempt", None)
            if callable(record_propose_attempt):
                record_propose_attempt(task.id, success=True)
            selected_attempt_meta = {
                "attempt": attempt_index,
                "source": candidate_source,
                "selected_model": candidate_model,
                "selected_temperature": candidate_temperature,
                "runtime_provider": runtime_provider,
                "model_context_length": model_context_length,
                "required_context_tokens": required_context_tokens,
            }
            propose_data = candidate_data
            break
        record_propose_attempt = getattr(loop, "_record_task_propose_attempt", None)
        if callable(record_propose_attempt):
            record_propose_attempt(task.id, success=False)
        raw_recovery_signal = candidate_snapshot.get(
            "model_recovery_signal"
        )
        terminal_recovery_signal = (
            sanitize_terminal_model_recovery_signal(
                raw_recovery_signal
            )
        )
        if terminal_recovery_signal is not None:
            verified_terminal_model_signal_seen = True
        fallback_decisions = [
            dict(item)
            for item in list(
                candidate_snapshot.get("fallback_decisions")
                or []
            )[-16:]
            if isinstance(item, dict)
        ]
        terminal_denial_seen = any(
            bool(item.get("terminal"))
            and not is_recoverable_model_error_type(
                item.get("trigger")
            )
            for item in fallback_decisions
        )
        terminal_signal_seen = bool(
            candidate_snapshot.get(
                "terminal_model_recovery_signal_seen"
            )
        ) or (
            isinstance(raw_recovery_signal, dict)
            and raw_recovery_signal.get("terminal") is True
        )
        if terminal_denial_seen or (
            terminal_signal_seen
            and terminal_recovery_signal is None
        ):
            rejected_terminal_model_signal_seen = True
        strategy_failures.append(
            {
                "attempt": attempt_index,
                "model": candidate_model,
                "temperature": candidate_temperature,
                "source": candidate_source,
                "reason": str(candidate_snapshot.get("reason") or "autopilot_no_executable_step"),
                "runtime_provider": runtime_provider,
                "model_context_length": model_context_length,
                "required_context_tokens": required_context_tokens,
                "failure_type": "invalid_proposal",
                "raw_preview": candidate_snapshot.get("raw_preview"),
                **(
                    {
                        "model_recovery_signal": dict(
                            terminal_recovery_signal
                        )
                    }
                    if terminal_recovery_signal is not None
                    else {}
                ),
                **(
                    {
                        "fallback_decisions": [
                            dict(item)
                            for item in fallback_decisions
                        ]
                    }
                    if fallback_decisions
                    else {}
                ),
            }
        )
        if (
            terminal_recovery_signal is not None
            or terminal_denial_seen
            or terminal_signal_seen
        ):
            append_trace_event(
                task.id,
                "autopilot_terminal_model_chain_observed",
                delegated_to=target_worker.url,
                attempt=attempt_index,
                terminal_reason=(
                    (
                        terminal_recovery_signal or {}
                    ).get("terminal_reason")
                    or "terminal_model_denial"
                ),
                recovery_eligible=(
                    terminal_recovery_signal is not None
                ),
            )
            break
    return ProposalStrategyOutcome(
        propose_data=propose_data,
        strategy_cfg=strategy_cfg,
        strategy_failures=strategy_failures,
        collected_llm_profiles=collected_llm_profiles,
        selected_attempt_meta=selected_attempt_meta,
        rejected_terminal_model_signal_seen=rejected_terminal_model_signal_seen,
        verified_terminal_model_signal_seen=verified_terminal_model_signal_seen,
    )
