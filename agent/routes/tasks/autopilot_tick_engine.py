from __future__ import annotations

import logging
import time
from typing import Any, Callable

from agent.config import settings
from agent.llm_integration import probe_lmstudio_runtime, probe_ollama_runtime
from agent.llm_benchmarks import recommend_model_for_context, recommend_models_for_context
from agent.services.repository_registry import get_repository_registry
from agent.services.task_template_resolution import resolve_task_role_template
from agent.tool_guardrails import estimate_text_tokens
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


def _strategy_cfg(loop: Any) -> dict[str, Any]:
    cfg = loop._agent_config() or {}
    return {
        "max_attempts": max(1, min(int(cfg.get("autopilot_strategy_max_attempts") or 3), 8)),
        "cooldown_seconds": max(0, min(int(cfg.get("autopilot_strategy_retry_delay_seconds") or 20), 600)),
        "fallback_models": _normalize_model_list(cfg.get("autopilot_strategy_fallback_models")),
        "temperature_profiles": _normalize_temperature_list(cfg.get("autopilot_strategy_temperature_profiles")),
        "adaptive_top_k": max(1, min(int(cfg.get("adaptive_model_routing_top_k") or 3), 10)),
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

    return {"runtime": runtime, "models": capabilities}


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
        candidate = role_overrides[role_name.lower()]
        if candidate not in excluded:
            selected_model = candidate
        source = "role_model_overrides"
    elif template_name and template_name.lower() in template_overrides:
        candidate = template_overrides[template_name.lower()]
        if candidate not in excluded:
            selected_model = candidate
        source = "template_model_overrides:name"
    elif template_id and template_id.lower() in template_overrides:
        candidate = template_overrides[template_id.lower()]
        if candidate not in excluded:
            selected_model = candidate
        source = "template_model_overrides:id"
    elif task_kind and task_kind in task_kind_overrides:
        candidate = task_kind_overrides[task_kind]
        if candidate not in excluded:
            selected_model = candidate
        source = "task_kind_model_overrides"
    elif bool(cfg.get("adaptive_model_routing_enabled", True)):
        try:
            app = getattr(loop, "_app", None)
            data_dir = (getattr(app, "config", {}) or {}).get("DATA_DIR") or "data"
            learned = recommend_model_for_context(
                data_dir=data_dir,
                task_kind=task_kind or "analysis",
                role_name=role_name or None,
                template_name=template_name or None,
                min_samples=int(cfg.get("adaptive_model_routing_min_samples") or 3),
            )
            if isinstance(learned, dict) and str(learned.get("model") or "").strip():
                candidate = str(learned.get("model") or "").strip()
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

    if bool(cfg.get("adaptive_model_routing_enabled", True)):
        try:
            app = getattr(loop, "_app", None)
            data_dir = (getattr(app, "config", {}) or {}).get("DATA_DIR") or "data"
            learned = recommend_models_for_context(
                data_dir=data_dir,
                task_kind=task_kind or "analysis",
                role_name=role_name,
                template_name=template_name,
                min_samples=int(cfg.get("adaptive_model_routing_min_samples") or 3),
                limit=int(strategy_cfg["adaptive_top_k"]),
                exclude_models=list(failed_models),
            )
            for entry in learned:
                model = str((entry or {}).get("model") or "").strip()
                if model:
                    _queue_model(model, "benchmark_context_learning:ranked")
        except Exception:
            pass

    for model in list(strategy_cfg["fallback_models"]):
        if model not in failed_models:
            _queue_model(model, "autopilot_strategy_fallback_models")

    default_model = str(cfg.get("default_model") or cfg.get("model") or "").strip()
    if default_model and default_model not in failed_models:
        _queue_model(default_model, "agent_default_model")

    _queue_model(None, "worker_default_no_override")
    for temperature in effective_temperatures:
        for model, source in ordered_models:
            _append(model, source, temperature)
    max_attempts = int(strategy_cfg["max_attempts"])
    return candidates[:max_attempts]


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
    runtime_caps = _runtime_model_capabilities(loop)
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
            selected_attempt_meta: dict[str, Any] = {}
            required_context_tokens = max(
                1024,
                estimate_text_tokens(getattr(task, "title", None))
                + estimate_text_tokens(getattr(task, "description", None))
                + 768,
            )
            for attempt_index, candidate in enumerate(strategy_candidates, start=1):
                propose_payload: dict[str, Any] = {"task_id": task.id}
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
                try:
                    candidate_data = loop._forward_with_retry(
                        target_worker.url,
                        f"/tasks/{task.id}/step/propose",
                        propose_payload,
                        token=target_worker.token,
                    )
                    candidate_data = services.autopilot_decision_service.normalize_proposal_data(candidate_data)
                except Exception as strategy_exc:
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
                if candidate_snapshot.get("command") or candidate_snapshot.get("tool_calls"):
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
                strategy_cfg = _strategy_cfg(loop)
                cooldown_seconds = int(strategy_cfg["cooldown_seconds"])
                now_ts = time.time()
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
                verification_status = {
                    **dict(getattr(task, "verification_status", None) or {}),
                    "autopilot_strategy": {
                        "attempt_count": int(strategy_state.get("attempt_count") or 0) + len(strategy_failures),
                        "failed_models": failed_models,
                        "failed_temperatures": failed_temperatures,
                        "failed_sources": failed_sources,
                        "runtime": dict(runtime_caps.get("runtime") or {}),
                        "last_failures": strategy_failures[-5:],
                        "last_failed_at": now_ts,
                        "next_retry_after": (now_ts + cooldown_seconds) if cooldown_seconds > 0 else now_ts,
                    },
                }
                update_local_task_status(
                    task.id,
                    "todo",
                    error="autopilot_strategy_exhausted",
                    verification_status=verification_status,
                    manual_override_until=(now_ts + cooldown_seconds) if cooldown_seconds > 0 else None,
                    last_proposal={"strategy_failures": strategy_failures[-5:]},
                )
                append_trace_event(
                    task.id,
                    "autopilot_strategy_exhausted",
                    delegated_to=target_worker.url,
                    failures=strategy_failures[-5:],
                    cooldown_seconds=cooldown_seconds,
                )
                loop.failed_count += 1
                continue

            model_meta.update(selected_attempt_meta)
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
        if isinstance(model_meta, dict):
            proposal_snapshot["model_selection"] = dict(model_meta)
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
