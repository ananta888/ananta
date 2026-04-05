from __future__ import annotations

import time

from flask import current_app, request

from agent.llm_benchmarks import (
    BENCH_TASK_KINDS,
    DEFAULT_BENCH_MODEL_ORDER,
    DEFAULT_BENCH_PROVIDER_ORDER,
    DEFAULT_BENCH_RETENTION,
    benchmark_identity_precedence_config,
    benchmark_retention_config,
    benchmark_rows,
)
from agent.llm_integration import _list_lmstudio_candidates
from agent.local_llm_backends import resolve_local_openai_backend
from agent.services.context_bundle_service import normalize_context_bundle_policy_config, resolve_context_bundle_policy

_LMSTUDIO_CATALOG_CACHE: dict[str, dict] = {}
_LMSTUDIO_CATALOG_CACHE_MAX_ENTRIES = 64
_BENCH_TASK_KINDS = BENCH_TASK_KINDS
_DEFAULT_BENCH_MODEL_ORDER = DEFAULT_BENCH_MODEL_ORDER
_DEFAULT_BENCH_PROVIDER_ORDER = DEFAULT_BENCH_PROVIDER_ORDER
_DEFAULT_BENCH_RETENTION = DEFAULT_BENCH_RETENTION
_SENSITIVE_CONFIG_KEYS = {"token", "secret", "password", "api_key"}
_HUB_COPILOT_ALLOWED_MODES = {"planning_only", "planning_and_routing"}


def normalize_artifact_flow_config(value: dict | None) -> dict:
    payload = dict(value or {})
    try:
        rag_top_k = int(payload.get("rag_top_k", 3))
    except (TypeError, ValueError):
        rag_top_k = 3
    rag_top_k = max(1, min(20, rag_top_k))
    try:
        max_tasks = int(payload.get("max_tasks", 30))
    except (TypeError, ValueError):
        max_tasks = 30
    max_tasks = max(1, min(200, max_tasks))
    try:
        max_worker_jobs_per_task = int(payload.get("max_worker_jobs_per_task", 5))
    except (TypeError, ValueError):
        max_worker_jobs_per_task = 5
    max_worker_jobs_per_task = max(1, min(20, max_worker_jobs_per_task))
    return {
        "enabled": bool(payload.get("enabled", True)),
        "rag_enabled": bool(payload.get("rag_enabled", True)),
        "rag_top_k": rag_top_k,
        "rag_include_content": bool(payload.get("rag_include_content", False)),
        "max_tasks": max_tasks,
        "max_worker_jobs_per_task": max_worker_jobs_per_task,
    }


def artifact_flow_settings_summary(cfg: dict) -> dict:
    requested = normalize_artifact_flow_config((cfg or {}).get("artifact_flow") if isinstance(cfg, dict) else {})
    return {
        "requested": requested,
        "effective": requested,
        "source": {
            "enabled": "artifact_flow.enabled",
            "rag_enabled": "artifact_flow.rag_enabled",
            "rag_top_k": "artifact_flow.rag_top_k",
            "rag_include_content": "artifact_flow.rag_include_content",
            "max_tasks": "artifact_flow.max_tasks",
            "max_worker_jobs_per_task": "artifact_flow.max_worker_jobs_per_task",
        },
    }


def parse_bool_query_flag(value: str | None) -> bool:
    normalized = str(value or "").strip().lower()
    return normalized in {"1", "true", "yes", "y", "on"}


def provider_alias(provider: str | None, agent_cfg: dict | None = None, provider_urls: dict | None = None) -> str:
    value = str(provider or "").strip().lower()
    local_backend = resolve_local_openai_backend(value, agent_cfg=agent_cfg, provider_urls=provider_urls)
    if local_backend:
        return str(local_backend.get("transport_provider") or "openai")
    return "openai" if value == "codex" else value


def resolve_provider_base_url(
    provider: str | None,
    requested_base_url: str | None,
    llm_cfg: dict | None,
    agent_cfg: dict | None,
    provider_urls: dict | None,
) -> tuple[str | None, str]:
    requested = str(requested_base_url or "").strip()
    llm_cfg = llm_cfg or {}
    agent_cfg = agent_cfg or {}
    provider_urls = provider_urls or {}
    normalized_provider = str(provider or "").strip().lower()
    local_backend = resolve_local_openai_backend(normalized_provider, agent_cfg=agent_cfg, provider_urls=provider_urls)
    provider_lookup = provider_alias(normalized_provider, agent_cfg=agent_cfg, provider_urls=provider_urls)

    if requested:
        return requested, "request.config.base_url"
    llm_cfg_base_url = str(llm_cfg.get("base_url") or "").strip()
    if llm_cfg_base_url:
        return llm_cfg_base_url, "agent_config.llm_config.base_url"
    if local_backend and local_backend.get("base_url"):
        return local_backend.get("base_url"), f"local_openai_backends.{local_backend['provider']}"
    if normalized_provider:
        if provider_urls.get(normalized_provider):
            return provider_urls.get(normalized_provider), f"provider_urls.{normalized_provider}"
        if provider_urls.get(provider_lookup):
            return provider_urls.get(provider_lookup), f"provider_urls.{provider_lookup}"
        if agent_cfg.get(f"{normalized_provider}_url"):
            return agent_cfg.get(f"{normalized_provider}_url"), f"agent_config.{normalized_provider}_url"
        if agent_cfg.get(f"{provider_lookup}_url"):
            return agent_cfg.get(f"{provider_lookup}_url"), f"agent_config.{provider_lookup}_url"
    return None, "provider_urls"


def resolve_provider_api_key(
    provider: str | None,
    explicit_api_key: str | None,
    api_key_profile: str | None,
    agent_cfg: dict | None,
) -> str | None:
    api_key = str(explicit_api_key or "").strip() or None
    if api_key:
        return api_key

    provider_name = str(provider or "").strip().lower()
    profile_name = str(api_key_profile or "").strip()
    agent_cfg = agent_cfg or {}
    if provider_name == "lmstudio":
        return "sk-no-key-needed"
    if profile_name:
        profiles = agent_cfg.get("llm_api_key_profiles") or {}
        selected_profile = profiles.get(profile_name) if isinstance(profiles, dict) else None
        if isinstance(selected_profile, str):
            return selected_profile.strip() or None
        if isinstance(selected_profile, dict):
            profile_provider = str(selected_profile.get("provider") or "").strip().lower()
            if not profile_provider or profile_provider in {provider_name, provider_alias(provider_name, agent_cfg=agent_cfg)}:
                value = str(selected_profile.get("api_key") or "").strip()
                if value:
                    return value
    local_backend = resolve_local_openai_backend(provider_name, agent_cfg=agent_cfg)
    if local_backend:
        local_api_key = str(local_backend.get("api_key") or "").strip()
        if local_api_key:
            return local_api_key
        local_profile = str(local_backend.get("api_key_profile") or "").strip()
        if local_profile and isinstance(agent_cfg.get("llm_api_key_profiles"), dict):
            selected_profile = (agent_cfg.get("llm_api_key_profiles") or {}).get(local_profile)
            if isinstance(selected_profile, str):
                return selected_profile.strip() or None
            if isinstance(selected_profile, dict):
                return str(selected_profile.get("api_key") or "").strip() or None
        return "sk-no-key-needed"
    return None


def sanitize_assistant_config(value):
    if isinstance(value, dict):
        cleaned = {}
        for key, nested_value in value.items():
            if any(s in str(key).lower() for s in _SENSITIVE_CONFIG_KEYS):
                cleaned[key] = "***"
            else:
                cleaned[key] = sanitize_assistant_config(nested_value)
        return cleaned
    if isinstance(value, list):
        return [sanitize_assistant_config(item) for item in value]
    return value


def normalize_hub_copilot_config(value: dict | None) -> dict:
    payload = dict(value or {})
    strategy_mode = str(payload.get("strategy_mode") or "planning_only").strip().lower() or "planning_only"
    if strategy_mode not in _HUB_COPILOT_ALLOWED_MODES:
        strategy_mode = "planning_only"
    temperature = payload.get("temperature")
    try:
        temperature = float(temperature) if temperature is not None else None
    except (TypeError, ValueError):
        temperature = None
    if temperature is not None:
        temperature = max(0.0, min(2.0, temperature))
    return {
        "enabled": bool(payload.get("enabled", False)),
        "provider": str(payload.get("provider") or "").strip().lower(),
        "model": str(payload.get("model") or "").strip(),
        "base_url": str(payload.get("base_url") or "").strip(),
        "temperature": temperature,
        "strategy_mode": strategy_mode,
    }


def hub_copilot_settings_summary(cfg: dict) -> dict:
    hub_cfg = normalize_hub_copilot_config((cfg or {}).get("hub_copilot") if isinstance(cfg, dict) else {})
    llm_cfg = (cfg or {}).get("llm_config", {}) if isinstance((cfg or {}).get("llm_config"), dict) else {}
    effective_provider = (
        hub_cfg["provider"] or str(llm_cfg.get("provider") or cfg.get("default_provider") or "").strip().lower() or None
    )
    effective_model = hub_cfg["model"] or str(llm_cfg.get("model") or cfg.get("default_model") or "").strip() or None
    effective_base_url = hub_cfg["base_url"] or str(llm_cfg.get("base_url") or "").strip() or None
    effective_temperature = hub_cfg["temperature"]
    if effective_temperature is None:
        fallback_temperature = llm_cfg.get("temperature")
        try:
            effective_temperature = float(fallback_temperature) if fallback_temperature is not None else None
        except (TypeError, ValueError):
            effective_temperature = None
    return {
        "enabled": hub_cfg["enabled"],
        "strategy_mode": hub_cfg["strategy_mode"],
        "active": bool(hub_cfg["enabled"] and effective_provider and effective_model),
        "requested": {
            "provider": hub_cfg["provider"] or None,
            "model": hub_cfg["model"] or None,
            "base_url": hub_cfg["base_url"] or None,
            "temperature": hub_cfg["temperature"],
        },
        "effective": {
            "provider": effective_provider,
            "model": effective_model,
            "base_url": effective_base_url,
            "temperature": effective_temperature,
        },
        "source": {
            "provider": "hub_copilot.provider"
            if hub_cfg["provider"]
            else ("agent_config.llm_config.provider" if llm_cfg.get("provider") else "agent_config.default_provider"),
            "model": "hub_copilot.model"
            if hub_cfg["model"]
            else ("agent_config.llm_config.model" if llm_cfg.get("model") else "agent_config.default_model"),
            "base_url": "hub_copilot.base_url"
            if hub_cfg["base_url"]
            else ("agent_config.llm_config.base_url" if llm_cfg.get("base_url") else None),
            "temperature": "hub_copilot.temperature"
            if hub_cfg["temperature"] is not None
            else ("agent_config.llm_config.temperature" if llm_cfg.get("temperature") is not None else None),
        },
    }


def context_bundle_policy_settings_summary(cfg: dict) -> dict:
    requested = normalize_context_bundle_policy_config((cfg or {}).get("context_bundle_policy") if isinstance(cfg, dict) else {})
    effective = resolve_context_bundle_policy(requested)
    return {
        "requested": requested,
        "effective": effective,
        "source": {
            "mode": "context_bundle_policy.mode",
            "compact_max_chunks": "context_bundle_policy.compact_max_chunks",
            "standard_max_chunks": "context_bundle_policy.standard_max_chunks",
        },
    }


def lmstudio_catalog_runtime_options() -> tuple[int, int, bool]:
    cfg = current_app.config.get("AGENT_CONFIG", {}) or {}
    provider_catalog_cfg = cfg.get("provider_catalog", {}) or {}
    timeout_default = int(provider_catalog_cfg.get("lmstudio_timeout_seconds") or 5)
    ttl_default = int(provider_catalog_cfg.get("cache_ttl_seconds") or 15)
    timeout_seconds = int(request.args.get("lmstudio_timeout_seconds") or timeout_default or 5)
    timeout_seconds = max(1, min(60, timeout_seconds))
    cache_ttl_seconds = int(request.args.get("cache_ttl_seconds") or ttl_default or 15)
    cache_ttl_seconds = max(0, min(3600, cache_ttl_seconds))
    force_refresh = parse_bool_query_flag(request.args.get("force_refresh"))
    return timeout_seconds, cache_ttl_seconds, force_refresh


def get_lmstudio_candidates_cached(
    lmstudio_url: str | None,
    *,
    timeout_seconds: int,
    cache_ttl_seconds: int,
    force_refresh: bool,
) -> list[dict]:
    if not lmstudio_url:
        return []

    now = time.time()
    cache_key = f"{lmstudio_url}|t={timeout_seconds}"
    cached = _LMSTUDIO_CATALOG_CACHE.get(cache_key) or {}
    cached_items = cached.get("items")
    cached_ts = float(cached.get("ts") or 0.0)
    if (
        not force_refresh
        and cache_ttl_seconds > 0
        and isinstance(cached_items, list)
        and (now - cached_ts) <= cache_ttl_seconds
    ):
        return cached_items

    try:
        fresh_items = _list_lmstudio_candidates(lmstudio_url, timeout=timeout_seconds)
    except Exception:
        fresh_items = []

    prune_lmstudio_catalog_cache(now=now)
    _LMSTUDIO_CATALOG_CACHE[cache_key] = {"ts": now, "items": fresh_items}
    return fresh_items


def catalog_models_for_local_backend(
    backend: dict,
    *,
    timeout_seconds: int,
    cache_ttl_seconds: int,
    force_refresh: bool,
) -> list[dict]:
    candidates = get_lmstudio_candidates_cached(
        backend.get("base_url"),
        timeout_seconds=timeout_seconds,
        cache_ttl_seconds=cache_ttl_seconds,
        force_refresh=force_refresh,
    )
    if candidates:
        return candidates
    return [{"id": model_id, "context_length": None} for model_id in (backend.get("configured_models") or [])]


def prune_lmstudio_catalog_cache(now: float | None = None) -> None:
    now = float(now or time.time())
    ttl_cutoff = now - 3600.0
    for key, value in list(_LMSTUDIO_CATALOG_CACHE.items()):
        ts = float((value or {}).get("ts") or 0.0)
        if ts <= 0.0 or ts < ttl_cutoff:
            _LMSTUDIO_CATALOG_CACHE.pop(key, None)
    overflow = len(_LMSTUDIO_CATALOG_CACHE) - _LMSTUDIO_CATALOG_CACHE_MAX_ENTRIES + 1
    if overflow <= 0:
        return
    oldest = sorted(_LMSTUDIO_CATALOG_CACHE.items(), key=lambda item: float((item[1] or {}).get("ts") or 0.0))
    for key, _ in oldest[:overflow]:
        _LMSTUDIO_CATALOG_CACHE.pop(key, None)


def benchmark_retention_settings() -> dict:
    return benchmark_retention_config(current_app.config.get("AGENT_CONFIG", {}) or {})


def benchmark_identity_precedence_settings() -> dict:
    return benchmark_identity_precedence_config(current_app.config.get("AGENT_CONFIG", {}) or {})


def benchmark_rows_for_task(task_kind: str | None = None, top_n: int | None = None) -> tuple[list[dict], dict]:
    return benchmark_rows(data_dir=current_app.config.get("DATA_DIR") or "data", task_kind=task_kind, top_n=top_n)


def runtime_model_available(
    provider: str,
    model: str,
    *,
    agent_cfg: dict,
    provider_urls: dict,
    timeout_seconds: int = 5,
) -> bool:
    provider = str(provider or "").strip().lower()
    model = str(model or "").strip()
    if not provider or not model:
        return False
    local_backend = resolve_local_openai_backend(provider, agent_cfg=agent_cfg, provider_urls=provider_urls)
    if local_backend:
        candidates = catalog_models_for_local_backend(
            local_backend,
            timeout_seconds=timeout_seconds,
            cache_ttl_seconds=15,
            force_refresh=False,
        )
        return any(str(item.get("id") or "").strip() == model for item in candidates)
    if provider == "ollama":
        return model in {"llama3", "mistral"} and bool(provider_urls.get("ollama"))
    if provider in {"openai", "codex"}:
        return model in {"gpt-4o", "gpt-4-turbo", "gpt-5-codex", "gpt-5-codex-mini"} and bool(
            provider_urls.get("openai") or current_app.config.get("OPENAI_API_KEY")
        )
    if provider == "anthropic":
        return model == "claude-3-5-sonnet-20240620" and bool(
            provider_urls.get("anthropic") or current_app.config.get("ANTHROPIC_API_KEY")
        )
    return False


def recommend_runtime_selection(
    *,
    task_kind: str,
    current_provider: str | None,
    current_model: str | None,
    agent_cfg: dict,
    provider_urls: dict,
) -> dict | None:
    rows, _ = benchmark_rows_for_task(task_kind=task_kind, top_n=10)
    for row in rows:
        provider = str(row.get("provider") or "").strip().lower()
        model = str(row.get("model") or "").strip()
        if runtime_model_available(provider, model, agent_cfg=agent_cfg, provider_urls=provider_urls):
            return {
                "provider": provider,
                "model": model,
                "selection_source": "benchmarks_available_top_ranked",
                "replaces": {"provider": current_provider, "model": current_model},
            }
    return None


def dashboard_benchmark_recommendation(*, task_kind: str, cfg: dict) -> dict:
    llm_cfg = (cfg or {}).get("llm_config", {}) if isinstance((cfg or {}).get("llm_config"), dict) else {}
    current_provider = str(llm_cfg.get("provider") or cfg.get("default_provider") or "").strip().lower() or None
    current_model = str(llm_cfg.get("model") or cfg.get("default_model") or "").strip() or None
    recommendation = recommend_runtime_selection(
        task_kind=task_kind,
        current_provider=current_provider,
        current_model=current_model,
        agent_cfg=current_app.config.get("AGENT_CONFIG", {}) or {},
        provider_urls=current_app.config.get("PROVIDER_URLS", {}) or {},
    )
    return {
        "current": {"provider": current_provider, "model": current_model},
        "recommended": recommendation,
        "has_explicit_override": bool(llm_cfg.get("provider") or llm_cfg.get("model")),
        "is_recommendation_active": bool(
            recommendation
            and recommendation.get("provider") == current_provider
            and recommendation.get("model") == current_model
        ),
    }
