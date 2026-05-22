from __future__ import annotations

import concurrent.futures
import contextlib
import logging
import math
import time
from dataclasses import dataclass, field
from typing import Any, Callable

_runtime_caps_cache: dict[str, Any] = {}
_runtime_caps_ts: float = 0.0
_RUNTIME_CAPS_TTL = 30.0

from agent.config import settings
from agent.llm_integration import probe_lmstudio_runtime, probe_ollama_runtime
from agent.llm_benchmarks import recommend_model_for_context, recommend_models_for_context
from agent.model_selection import normalize_legacy_model_name
from agent.services.repository_registry import get_repository_registry
from agent.services.task_template_resolution import resolve_task_role_template
from agent.tool_guardrails import estimate_text_tokens
from agent.routes.tasks.autopilot_dispatch_policy import (
    build_tick_debug_payload,
    classify_no_candidate_reason,
    dispatch_queue_positions,
    resolve_effective_concurrency,
    resolve_target_worker_for_task,
)
from agent.metrics import (
    DISPATCH_WAIT_SECONDS,
    STRATEGY_ATTEMPT_COUNT,
    TASK_FAILURE_REASON_COUNT,
    TASK_QUEUE_WAIT_SECONDS,
    TASK_SUCCESS_RATE,
    WORKER_BUSY_SECONDS,
    WORKER_PROPOSE_DURATION_SECONDS,
    WORKSPACE_WRITE_CONFLICT_COUNT,
)
from agent.services.goal_config_runtime_service import get_goal_config_runtime_service


@dataclass
class TaskDispatchResult:
    task_id: str
    dispatched: bool = False
    completed: bool = False
    failed: bool = False
    failure_type: str | None = None


def _current_task_status(task_id: str, *, app: Any) -> str:
    repos = get_repository_registry(app)
    current = repos.task_repo.get_by_id(task_id)
    return str(getattr(current, "status", "") or "").strip().lower()


def _is_terminal_status(status: str) -> bool:
    return status in {"completed", "failed", "cancelled"}


def _should_terminalize_no_executable_strategy(strategy_failures: list[dict[str, Any]]) -> bool:
    """Return True when the strategy chain has already proven it cannot yield an executable step.

    These failures are not likely to be fixed by the next tick with the same task context,
    so the task should move to a terminal review state instead of re-entering todo.
    """
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

    # Use goal-global task view as fallback when scoped task list is empty.
    # This prevents accidental re-planning/duplication when scope filters
    # (team/cursor transitions) temporarily hide existing goal tasks.
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

    # Don't trigger recovery replanning when tasks are merely blocked by dependencies —
    # they will unblock as their predecessors complete. Replanning here only creates
    # duplicate tasks on top of the existing blocked ones.
    has_blocked = any(str(getattr(task, "status", "") or "").strip().lower() == "blocked_by_dependency" for task in non_terminal)
    if has_blocked:
        return False

    # Don't trigger recovery replanning when todo tasks exist — they were just
    # unblocked by reconcile_dependencies and will be dispatched on the next tick.
    has_todo = any(str(getattr(task, "status", "") or "").strip().lower() in {"todo", "created"} for task in non_terminal)
    if has_todo:
        return False

    # Don't re-plan when tasks are awaiting human review — they represent a
    # deliberate pause, not a planning gap.
    has_review_pending = any(
        str(getattr(task, "status", "") or "").strip().lower() in {"waiting_for_review", "needs_review"}
        for task in non_terminal
    )
    if has_review_pending:
        return False

    # When ALL tasks are terminal and none completed, re-planning produces the same
    # plan against the same context — it won't fix the underlying failure. Fail the
    # goal immediately rather than burning max_attempts re-plan cycles.
    if task_view and not non_terminal:
        all_failed = all(
            str(getattr(t, "status", "") or "").strip().lower() == "failed" for t in task_view
        )
        if all_failed:
            try:
                services.goal_lifecycle_service.transition_goal(
                    goal,
                    target_status="failed",
                    reason="all_tasks_failed_no_recovery",
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
        # If planning has exhausted recovery attempts and still has no dispatchable
        # candidates, terminate the goal explicitly to avoid indefinite planned hangs.
        stall_since = float(recovery.get("stall_since") or 0.0) or now_ts
        recovery.setdefault("stall_since", stall_since)
        stalled_for = max(0.0, now_ts - stall_since)
        stale_threshold_seconds = 120.0
        if attempts >= max_attempts and stalled_for >= stale_threshold_seconds:
            try:
                services.goal_lifecycle_service.transition_goal(
                    goal,
                    target_status="failed",
                    reason="planned_stall_no_dispatchable_candidates",
                )
                recovery.update(
                    {
                        "last_attempt_at": now_ts,
                        "last_reason": "planned_stall_no_dispatchable_candidates",
                        "stalled_for_seconds": int(stalled_for),
                    }
                )
                execution_preferences["autopilot_recovery"] = recovery
                goal.execution_preferences = execution_preferences
                repos.goal_repo.save(goal)
            except Exception:
                pass
        else:
            recovery.update(
                {
                    "last_attempt_at": now_ts,
                    "last_reason": "recovery_cooldown_or_exhausted",
                    "stalled_for_seconds": int(stalled_for),
                }
            )
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
            team_id=team_id,
            create_tasks=True,
            use_template=True,
            use_repo_context=True,
            goal_id=goal.id,
            goal_trace_id=str(getattr(goal, "trace_id", "") or ""),
            mode=str(getattr(goal, "mode", "") or "generic"),
            mode_data=dict(getattr(goal, "mode_data", None) or {}),
        )
        created_ids = list((plan_result or {}).get("created_task_ids") or [])
        recovery.update(
            {
                "attempts": attempts + 1,
                "last_attempt_at": now_ts,
                "last_reason": recovery_reason,
                "last_created_task_count": len(created_ids),
                "last_plan_id": (plan_result or {}).get("plan_id"),
            }
        )
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


def _fallback_policy(loop: Any) -> dict[str, Any]:
    cfg = (loop._agent_config() or {}).get("execution_fallback_policy", {}) or {}
    return {
        "allow_hub_worker_fallback": bool(cfg.get("allow_hub_worker_fallback", True)),
        "escalate_on_fallback_block": bool(cfg.get("escalate_on_fallback_block", True)),
        "fallback_block_status": str(cfg.get("fallback_block_status") or "blocked").strip().lower() or "blocked",
    }


def _normalize_override_map(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for key, value in raw.items():
        normalized_key = str(key or "").strip().lower()
        normalized_value = str(value or "").strip()
        if normalized_key and normalized_value:
            out[normalized_key] = normalized_value
    return out


def _normalize_model_list(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        model_name = str(item or "").strip()
        if not model_name or model_name in seen:
            continue
        seen.add(model_name)
        out.append(model_name)
    return out


def _normalize_temperature_value(value: Any) -> float | None:
    if value is None:
        return None
    try:
        normalized = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(normalized):
        return None
    if normalized < 0.0:
        normalized = 0.0
    if normalized > 2.0:
        normalized = 2.0
    return round(normalized, 3)


def _normalize_temperature_list(raw: Any) -> list[float]:
    if not isinstance(raw, list):
        return []
    out: list[float] = []
    seen: set[float] = set()
    for item in raw:
        normalized = _normalize_temperature_value(item)
        if normalized is None or normalized in seen:
            continue
        seen.add(normalized)
        out.append(normalized)
    return out


def _preferred_benchmark_provider(cfg: dict[str, Any]) -> str | None:
    llm_cfg = cfg.get("llm_config") if isinstance(cfg.get("llm_config"), dict) else {}
    for candidate in (llm_cfg.get("provider"), cfg.get("default_provider"), cfg.get("provider")):
        provider = str(candidate or "").strip().lower()
        if provider:
            return provider
    return None


def _normalize_model_candidate(model: str | None, *, cfg: dict[str, Any]) -> str | None:
    return normalize_legacy_model_name(model, provider=_preferred_benchmark_provider(cfg))


def _strategy_cfg(loop: Any) -> dict[str, Any]:
    cfg = loop._agent_config() or {}
    proposal_budget = dict(cfg.get("proposal_budget") or {})
    return {
        "max_attempts": max(1, min(int(cfg.get("autopilot_strategy_max_attempts") or 3), 8)),
        "cooldown_seconds": max(0, min(int(cfg.get("autopilot_strategy_retry_delay_seconds") or 20), 600)),
        "fallback_models": [
            normalized
            for item in _normalize_model_list(cfg.get("autopilot_strategy_fallback_models"))
            if (normalized := _normalize_model_candidate(item, cfg=cfg))
        ],
        "temperature_profiles": _normalize_temperature_list(cfg.get("autopilot_strategy_temperature_profiles")),
        "adaptive_top_k": max(1, min(int(cfg.get("adaptive_model_routing_top_k") or 3), 10)),
        "proposal_budget": {
            "max_total_seconds": max(10, min(int(proposal_budget.get("max_total_seconds") or 90), 1800)),
            "max_llm_calls": max(1, min(int(proposal_budget.get("max_llm_calls") or 2), 12)),
            "max_strategy_attempts": max(1, min(int(proposal_budget.get("max_strategy_attempts") or 2), 12)),
            "allow_parallel_strategy_race": bool(proposal_budget.get("allow_parallel_strategy_race", False)),
        },
    }


def _extract_strategy_state(task: Any) -> dict[str, Any]:
    verification = dict(getattr(task, "verification_status", None) or {})
    strategy = dict(verification.get("autopilot_strategy") or {})
    failed_models = _normalize_model_list(strategy.get("failed_models"))
    failed_temperatures = _normalize_temperature_list(strategy.get("failed_temperatures"))
    failed_sources = [str(item or "").strip() for item in list(strategy.get("failed_sources") or []) if str(item or "").strip()]
    return {
        "failed_models": failed_models,
        "failed_temperatures": failed_temperatures,
        "failed_sources": failed_sources,
        "attempt_count": max(0, int(strategy.get("attempt_count") or 0)),
    }


def _safe_context_length(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    if parsed <= 0:
        return None
    return parsed


def _runtime_model_capabilities(loop: Any) -> dict[str, Any]:
    global _runtime_caps_cache, _runtime_caps_ts
    if _runtime_caps_cache and (time.time() - _runtime_caps_ts) < _RUNTIME_CAPS_TTL:
        return _runtime_caps_cache

    app = getattr(loop, "_app", None)
    app_cfg = (getattr(app, "config", {}) or {})
    provider_urls = dict(app_cfg.get("PROVIDER_URLS") or {})
    llm_cfg = ((loop._agent_config() or {}).get("llm_config") or {})
    default_provider = str(llm_cfg.get("provider") or (loop._agent_config() or {}).get("default_provider") or "").strip().lower() or None
    default_provider = default_provider if default_provider in {"lmstudio", "ollama"} else None
    lmstudio_url = str(provider_urls.get("lmstudio") or "").strip()
    ollama_url = str(provider_urls.get("ollama") or "").strip()
    capabilities: dict[str, dict[str, Any]] = {}
    runtime = {
        "default_provider": default_provider,
        "lmstudio": {"ok": False, "candidate_count": 0},
        "ollama": {"ok": False, "candidate_count": 0},
    }
    timeout_seconds = 3

    if lmstudio_url:
        try:
            probe = probe_lmstudio_runtime(lmstudio_url, timeout=timeout_seconds)
            runtime["lmstudio"] = {"ok": bool(probe.get("ok")), "status": probe.get("status"), "candidate_count": int(probe.get("candidate_count") or 0)}
            for item in list(probe.get("candidates") or []):
                model_id = str((item or {}).get("id") or "").strip()
                if not model_id:
                    continue
                capabilities[model_id] = {
                    "provider": "lmstudio",
                    "context_length": _safe_context_length((item or {}).get("context_length")),
                }
        except Exception:
            pass

    if ollama_url:
        try:
            probe = probe_ollama_runtime(ollama_url, timeout=timeout_seconds)
            runtime["ollama"] = {"ok": bool(probe.get("ok")), "status": probe.get("status"), "candidate_count": int(probe.get("candidate_count") or 0)}
            for item in list(probe.get("models") or []):
                model_id = str((item or {}).get("name") or "").strip()
                if not model_id:
                    continue
                details = (item or {}).get("details") if isinstance((item or {}).get("details"), dict) else {}
                ctx = (
                    _safe_context_length((item or {}).get("context_length"))
                    or _safe_context_length((item or {}).get("num_ctx"))
                    or _safe_context_length(details.get("context_length"))
                    or _safe_context_length(details.get("num_ctx"))
                )
                capabilities[model_id] = {
                    "provider": "ollama",
                    "context_length": ctx,
                }
        except Exception:
            pass

    result = {"runtime": runtime, "models": capabilities}
    _runtime_caps_cache = result
    _runtime_caps_ts = time.time()
    return result


def _select_model_for_task(*, loop: Any, task: Any, excluded_models: set[str] | None = None) -> tuple[str | None, dict[str, Any]]:
    cfg = loop._agent_config() or {}
    role_overrides = _normalize_override_map(cfg.get("role_model_overrides"))
    template_overrides = _normalize_override_map(cfg.get("template_model_overrides"))
    task_kind_overrides = _normalize_override_map(cfg.get("task_kind_model_overrides"))
    excluded = {str(item or "").strip() for item in (excluded_models or set()) if str(item or "").strip()}

    task_kind = str(getattr(task, "task_kind", None) or "").strip().lower()
    repos = get_repository_registry()
    resolved = resolve_task_role_template(task, repos=repos)
    role_id = str(resolved.get("role_id") or "").strip()
    role_name = str(resolved.get("role_name") or "").strip()
    template_id = str(resolved.get("template_id") or "").strip()
    template_name = str(resolved.get("template_name") or "").strip()

    selected_model = None
    source = "none"
    if role_name and role_name.lower() in role_overrides:
        candidate = _normalize_model_candidate(role_overrides[role_name.lower()], cfg=cfg)
        if candidate not in excluded:
            selected_model = candidate
        source = "role_model_overrides"
    elif template_name and template_name.lower() in template_overrides:
        candidate = _normalize_model_candidate(template_overrides[template_name.lower()], cfg=cfg)
        if candidate not in excluded:
            selected_model = candidate
        source = "template_model_overrides:name"
    elif template_id and template_id.lower() in template_overrides:
        candidate = _normalize_model_candidate(template_overrides[template_id.lower()], cfg=cfg)
        if candidate not in excluded:
            selected_model = candidate
        source = "template_model_overrides:id"
    elif task_kind and task_kind in task_kind_overrides:
        candidate = _normalize_model_candidate(task_kind_overrides[task_kind], cfg=cfg)
        if candidate not in excluded:
            selected_model = candidate
        source = "task_kind_model_overrides"

    if selected_model is None:
        default_model_candidate = _normalize_model_candidate(str(cfg.get("default_model") or cfg.get("model") or "").strip(), cfg=cfg)
        if default_model_candidate and default_model_candidate not in excluded:
            selected_model = default_model_candidate
            source = "agent_default_model"

    if selected_model is None and bool(cfg.get("adaptive_model_routing_enabled", True)):
        try:
            app = getattr(loop, "_app", None)
            data_dir = (getattr(app, "config", {}) or {}).get("DATA_DIR") or "data"
            preferred_provider = _preferred_benchmark_provider(cfg)
            learned = recommend_model_for_context(
                data_dir=data_dir,
                task_kind=task_kind or "analysis",
                role_name=role_name or None,
                template_name=template_name or None,
                provider=preferred_provider,
                min_samples=int(cfg.get("adaptive_model_routing_min_samples") or 3),
            )
            if isinstance(learned, dict) and str(learned.get("model") or "").strip():
                candidate = _normalize_model_candidate(str(learned.get("model") or "").strip(), cfg=cfg)
                if candidate not in excluded:
                    selected_model = candidate
                    source = str(learned.get("selection_source") or "benchmark_context_learning")
                else:
                    source = "benchmark_context_learning:excluded"
        except Exception:
            pass

    return selected_model, {
        "selected_model": selected_model,
        "source": source,
        "role_id": role_id or None,
        "role_name": role_name or None,
        "template_id": template_id or None,
        "template_name": template_name or None,
        "task_kind": task_kind or None,
    }


def _proposal_strategy_candidates(*, loop: Any, task: Any, base_model_meta: dict[str, Any], state: dict[str, Any]) -> list[dict[str, Any]]:
    cfg = loop._agent_config() or {}
    strategy_cfg = _strategy_cfg(loop)
    failed_models = set(state.get("failed_models") or [])
    failed_temperatures = set(state.get("failed_temperatures") or [])
    role_name = str(base_model_meta.get("role_name") or "").strip() or None
    template_name = str(base_model_meta.get("template_name") or "").strip() or None
    task_kind = str(base_model_meta.get("task_kind") or "analysis").strip().lower() or "analysis"
    configured_default_temperature = _normalize_temperature_value((cfg.get("llm_config") or {}).get("temperature"))
    temperature_profiles = list(strategy_cfg.get("temperature_profiles") or [])
    effective_temperatures: list[float | None] = []
    if configured_default_temperature is not None and configured_default_temperature not in failed_temperatures:
        effective_temperatures.append(configured_default_temperature)
    for temp in temperature_profiles:
        if temp in failed_temperatures or temp in effective_temperatures:
            continue
        effective_temperatures.append(temp)
    if not effective_temperatures:
        effective_temperatures = [None]

    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def _append(model: str | None, source: str, temperature: float | None) -> None:
        normalized = str(model or "").strip() or None
        normalized_temperature = _normalize_temperature_value(temperature)
        if normalized_temperature is not None and normalized_temperature in failed_temperatures:
            return
        key = (normalized or "__none__", str(normalized_temperature))
        if key in seen:
            return
        seen.add(key)
        candidates.append({"model": normalized, "source": source, "temperature": normalized_temperature})

    ordered_models: list[tuple[str | None, str]] = []

    def _queue_model(model: str | None, source: str) -> None:
        normalized = str(model or "").strip() or None
        if normalized is not None and normalized in failed_models:
            return
        ordered_models.append((normalized, source))

    selected = str(base_model_meta.get("selected_model") or "").strip()
    if selected and selected not in failed_models:
        _queue_model(selected, str(base_model_meta.get("source") or "base_selection"))

    default_model = _normalize_model_candidate(str(cfg.get("default_model") or cfg.get("model") or "").strip(), cfg=cfg)
    if default_model and default_model not in failed_models and default_model != selected:
        _queue_model(default_model, "agent_default_model")

    opencode_default_model = _normalize_model_candidate(str(cfg.get("opencode_default_model") or "").strip(), cfg=cfg)
    if opencode_default_model and opencode_default_model not in failed_models:
        _queue_model(opencode_default_model, "opencode_default_model")

    if bool(cfg.get("adaptive_model_routing_enabled", True)):
        try:
            app = getattr(loop, "_app", None)
            data_dir = (getattr(app, "config", {}) or {}).get("DATA_DIR") or "data"
            preferred_provider = _preferred_benchmark_provider(cfg)
            learned = recommend_models_for_context(
                data_dir=data_dir,
                task_kind=task_kind or "analysis",
                role_name=role_name,
                template_name=template_name,
                provider=preferred_provider,
                min_samples=int(cfg.get("adaptive_model_routing_min_samples") or 3),
                limit=int(strategy_cfg["adaptive_top_k"]),
                exclude_models=list(failed_models),
            )
            for entry in learned:
                model = _normalize_model_candidate(str((entry or {}).get("model") or "").strip(), cfg=cfg)
                if model:
                    _queue_model(model, "benchmark_context_learning:ranked")
        except Exception:
            pass

    for model in list(strategy_cfg["fallback_models"]):
        if model not in failed_models:
            _queue_model(model, "autopilot_strategy_fallback_models")

    _queue_model(None, "worker_default_no_override")
    for temperature in effective_temperatures:
        for model, source in ordered_models:
            _append(model, source, temperature)
    max_attempts = int(strategy_cfg["max_attempts"])
    return candidates[:max_attempts]


def _task_log(task_id: str) -> logging.LoggerAdapter:
    """thr-009: Returns a LoggerAdapter that prefixes every log line with the task_id
    so parallel thread output stays attributable in mixed logs."""
    return logging.LoggerAdapter(logging.getLogger(__name__), {"task_id": task_id})


def _dispatch_one_task(  # noqa: C901
    *,
    task: Any,
    target_worker: Any,      # thr-010: pre-assigned by caller under _routing_lock
    was_assigned: bool,      # thr-010: whether assignment status update is needed
    loop: Any,
    services: Any,
    policy: dict,
    fallback_policy: dict,
    runtime_caps: dict,
    queue_positions: dict,
    local_worker_url: str,
    app: Any,                # thr-008: Flask app for per-thread app_context
    append_trace_event: Callable[..., None],
    update_local_task_status: Callable[..., None],
) -> TaskDispatchResult:
    """Execute the full propose→execute cycle for a single task.

    thr-005: Extracted from execute_autopilot_tick() for parallelisation.
    thr-006: Called from ThreadPoolExecutor threads.
    thr-007: Caller enforces a hard timeout via as_completed(); no internal timeout needed.
    thr-008: Opens its own Flask app_context so DB access works in the new thread.
    thr-009: All logging uses _task_log(task.id) so mixed output stays attributable.
    thr-010: target_worker/was_assigned are pre-assigned by the caller (no cursor race).
    """
    from agent.services.lmstudio_request_registry import set_thread_context, clear_thread_context
    _goal_id = str(getattr(task, "goal_id", "") or "").strip() or None
    _task_id = str(getattr(task, "id", "") or "").strip() or None
    set_thread_context(_goal_id, _task_id)
    try:
        # thr-008: each thread needs its own app context; parent's context is not inherited.
        _ctx = app.app_context() if app is not None else contextlib.nullcontext()
        with _ctx:
            return _dispatch_one_task_inner(
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
                append_trace_event=append_trace_event,
                update_local_task_status=update_local_task_status,
            )
    finally:
        clear_thread_context()


def _dispatch_one_task_inner(  # noqa: C901
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
) -> TaskDispatchResult:
    log = _task_log(task.id)  # thr-009
    result = TaskDispatchResult(task_id=task.id)
    # Skip stale dispatch candidates when a parallel thread already finalized
    # this task in the database.
    app_ctx = getattr(loop, "_app", None)
    latest_status = _current_task_status(task.id, app=app_ctx)
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
        return result

    # Skip dispatch if the parent goal is already in a terminal state.
    goal_id = str(getattr(task, "goal_id", "") or "").strip()
    if goal_id:
        repos = get_repository_registry(app_ctx)
        goal_obj = repos.goal_repo.get_by_id(goal_id)
        goal_status = str(getattr(goal_obj, "status", "") or "").strip().lower()
        if goal_status in {"completed", "failed", "cancelled", "aborted", "timeout"}:
            append_trace_event(
                task.id,
                "autopilot_dispatch_skipped_goal_terminal",
                delegated_to=target_worker.url,
                goal_status=goal_status,
            )
            result.dispatched = True
            result.failed = True
            result.failure_type = f"goal_{goal_status}"
            return result

    if was_assigned:
        latest_status = _current_task_status(task.id, app=app_ctx)
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
    else:
        # Throttle repeated propose attempts for already-assigned tasks.
        # Without this guard, tight autopilot ticks can flood propose calls,
        # quickly tripping hard-guard windows without meaningful progress.
        current_status = _current_task_status(task.id, app=app_ctx)
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
    current_status_for_scope = _current_task_status(task.id, app=app_ctx)
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

    model_meta: dict[str, Any] = {}
    strategy_state = _extract_strategy_state(task)
    try:
        selected_model, model_meta = _select_model_for_task(
            loop=loop,
            task=task,
            excluded_models=set(strategy_state.get("failed_models") or []),
        )
        model_meta["selected_model"] = selected_model
        strategy_candidates = _proposal_strategy_candidates(
            loop=loop,
            task=task,
            base_model_meta=model_meta,
            state=strategy_state,
        )
        propose_data = None
        strategy_failures: list[dict[str, Any]] = []
        collected_llm_profiles: list[dict[str, Any]] = []
        selected_attempt_meta: dict[str, Any] = {}
        required_context_tokens = max(
            1024,
            estimate_text_tokens(getattr(task, "title", None))
            + estimate_text_tokens(getattr(task, "description", None))
            + 768,
        )
        strategy_cfg = _strategy_cfg(loop)
        is_backpressure_active = getattr(loop, "_is_provider_backpressure_active", None)
        backpressure_details = getattr(loop, "_provider_backpressure_details", None)
        if callable(is_backpressure_active) and is_backpressure_active("ollama"):
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
        budget = dict(strategy_cfg.get("proposal_budget") or {})
        budget_started_at = time.time()
        max_total_seconds = int(budget.get("max_total_seconds") or 90)
        max_llm_calls = int(budget.get("max_llm_calls") or 2)
        max_strategy_attempts = int(budget.get("max_strategy_attempts") or 2)
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
        STRATEGY_ATTEMPT_COUNT.observe(float(len(strategy_candidates)))
        for attempt_index, candidate in enumerate(strategy_candidates, start=1):
            # Hard guard: never re-propose terminal tasks, even inside strategy loops.
            latest_status = _current_task_status(task.id, app=app_ctx)
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
                return result
            # Task-local propose backoff to prevent rapid propose storms.
            task_propose_backoff = getattr(loop, "_task_propose_backoff_details", None)
            if callable(task_propose_backoff):
                deferred, remaining_s = task_propose_backoff(task.id)
                if deferred:
                    append_trace_event(
                        task.id,
                        "autopilot_strategy_attempt_deferred_backoff",
                        delegated_to=target_worker.url,
                        attempt=attempt_index,
                        backoff_remaining_seconds=round(float(remaining_s), 3),
                    )
                    strategy_failures.append(
                        {
                            "attempt": attempt_index,
                            "reason": "task_propose_backoff_deferred",
                            "failure_type": "task_propose_backoff",
                            "backoff_remaining_seconds": round(float(remaining_s), 3),
                        }
                    )
                    break
            elapsed = time.time() - budget_started_at
            if elapsed > max_total_seconds:
                strategy_failures.append(
                    {
                        "attempt": attempt_index,
                        "reason": "proposal_budget_exhausted_total_seconds",
                        "elapsed_seconds": round(elapsed, 3),
                        "max_total_seconds": max_total_seconds,
                        "failure_type": "proposal_budget_exhausted",
                    }
                )
                break
            if (attempt_index - 1) >= max_llm_calls or (attempt_index - 1) >= max_strategy_attempts:
                strategy_failures.append(
                    {
                        "attempt": attempt_index,
                        "reason": "proposal_budget_exhausted_llm_calls",
                        "max_llm_calls": max_llm_calls,
                        "max_strategy_attempts": max_strategy_attempts,
                        "failure_type": "proposal_budget_exhausted",
                    }
                )
                break
            propose_payload: dict[str, Any] = {"task_id": task.id}
            autopilot_cfg = ((loop._agent_config() or {}).get("autopilot", {}) or {})
            strategy_mode_override = str(
                autopilot_cfg.get("strategy_mode_override") or "autopilot_no_human_review"
            ).strip().lower()
            if strategy_mode_override:
                propose_payload["strategy_mode"] = strategy_mode_override
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
            latest_before_propose = _current_task_status(task.id, app=loop._app)
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
                candidate_data = services.autopilot_decision_service.normalize_proposal_data(candidate_data)
            except Exception as strategy_exc:
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
                }
            )

        if propose_data is None:
            latest_status = _current_task_status(task.id, app=app_ctx)
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
            schedule_repair_retry = (
                terminalize_no_exec
                and not allow_human_review
                and repair_rounds < repair_budget
            )
            if schedule_repair_retry:
                retry_status = "todo"
                cooldown_seconds = repair_delay_seconds
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
            }
            retry_snapshot = _ensure_llm_profile_snapshot(
                snapshot={"strategy_failures": strategy_failures[-5:]},
                strategy_id=None,
                model_meta=model_meta if isinstance(model_meta, dict) else None,
                preferred_profile=collected_llm_profiles,
                allow_synthetic_fallback=bool(
                    ((loop._agent_config() or {}).get("llm_profile_policy") or {}).get("allow_synthetic_fallback", False)
                ),
            )
            update_local_task_status(
                task.id,
                retry_status,
                error="autopilot_strategy_exhausted",
                verification_status=verification_status,
                manual_override_until=(now_ts + cooldown_seconds) if cooldown_seconds > 0 else None,
                last_proposal=_merged_last_proposal_snapshot(
                    task_id=task.id,
                    snapshot=retry_snapshot,
                    app=app_ctx,
                ),
                force=True,
                event_type="autopilot_strategy_retry_scheduled",
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
                },
            )
            append_trace_event(
                task.id,
                "autopilot_strategy_exhausted",
                delegated_to=target_worker.url,
                failures=strategy_failures[-5:],
                cooldown_seconds=cooldown_seconds,
            )
            result.failed = True
            result.failure_type = "strategy_exhausted"
            return result

        model_meta.update(selected_attempt_meta)
    except Exception as e:
        if _is_transient_worker_transport_error(e):
            defer_until = time.time() + 30
            update_local_task_status(
                task.id,
                "todo",
                manual_override_until=defer_until,
                error=f"transient_worker_transport_error:{str(e)[:180]}",
            )
            append_trace_event(
                task.id,
                "autopilot_worker_transport_deferred",
                delegated_to=target_worker.url,
                reason=str(e),
                defer_seconds=30,
            )
            result.failed = True
            result.failure_type = "propose_transport_deferred"
            return result
        latest_status = _current_task_status(task.id, app=app_ctx)
        if _is_terminal_status(latest_status):
            append_trace_event(
                task.id,
                "autopilot_worker_failed_skipped_terminal",
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
        result.failure_type = "propose_exception"
        return result

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
        latest_status = _current_task_status(task.id, app=app_ctx)
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
            log.debug("Followup analysis skipped: %s", e)  # thr-009

    return result


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
    # In fully autonomous runs (allow_human_review=False) use "failed" instead of "todo"
    # to prevent infinite retry cycles when the same tooling issue repeats.
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
        _recovery_status = "todo" if _task_allow_human_review else "failed"
        _recovery_event = "recover_waiting_review_retryable_failure" if _task_allow_human_review else "waiting_for_review_auto_failed_no_human_review"
        update_local_task_status(
            _t.id,
            _recovery_status,
            error=None if _task_allow_human_review else "autonomous_run_no_human_review_auto_failed",
            event_type=_recovery_event,
            event_actor="autopilot_tick",
            force=True,
        )
        append_trace_event(
            _t.id,
            _recovery_event,
            reason="auto_retry_recoverable_waiting_review_failure" if _task_allow_human_review else "autonomous_run_waiting_for_review_terminated",
            allow_human_review=_task_allow_human_review,
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
        target_worker, was_assigned = loop._assign_worker(task, workers)
        if target_worker is None:
            loop._increment_failed()
            append_trace_event(task.id, "autopilot_no_worker", reason="no_worker_available")
            update_local_task_status(task.id, "failed", error="no_worker_available", force=True)
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
    async_dispatch_enabled = bool(((loop._agent_config() or {}).get("autopilot") or {}).get("async_dispatch_enabled", False))
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
