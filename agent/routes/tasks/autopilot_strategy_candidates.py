from __future__ import annotations

import time
from typing import Any

from agent.llm_benchmarks import recommend_models_for_context
from agent.llm_integration import probe_lmstudio_runtime, probe_ollama_runtime
from agent.routes.tasks.autopilot_model_selector import (
    _normalize_model_candidate,
    _normalize_model_list,
    _normalize_temperature_list,
    _normalize_temperature_value,
    _preferred_benchmark_provider,
)

_runtime_caps_cache: dict[str, Any] = {}
_runtime_caps_ts: float = 0.0
_RUNTIME_CAPS_TTL = 30.0


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
