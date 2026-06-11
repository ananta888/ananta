from __future__ import annotations

import json
import logging
import os
from typing import Any

from flask import Flask

from agent.config import settings


def _provider_alias(provider: str | None) -> str:
    value = str(provider or "").strip().lower()
    return "openai" if value == "codex" else value


def _sync_default_provider_settings(lc: dict) -> str | None:
    prov = lc.get("provider")
    if prov and hasattr(settings, "default_provider"):
        settings.default_provider = prov
    if lc.get("model") and hasattr(settings, "default_model"):
        settings.default_model = lc.get("model")
    if lc.get("lmstudio_api_mode") and hasattr(settings, "lmstudio_api_mode"):
        settings.lmstudio_api_mode = lc.get("lmstudio_api_mode")
    return prov


def _sync_provider_connection_settings(app: Flask, prov: str, lc: dict) -> None:
    effective_provider = _provider_alias(prov)
    if lc.get("base_url"):
        app.config["PROVIDER_URLS"][prov] = lc.get("base_url")
        if prov == "codex":
            app.config["PROVIDER_URLS"]["openai"] = lc.get("base_url")
        url_attr = f"{effective_provider}_url"
        if hasattr(settings, url_attr):
            setattr(settings, url_attr, lc.get("base_url"))

    if not lc.get("api_key"):
        return
    key_attr = f"{effective_provider}_api_key"
    if hasattr(settings, key_attr):
        setattr(settings, key_attr, lc.get("api_key"))
    if prov in {"openai", "codex"}:
        app.config["OPENAI_API_KEY"] = lc.get("api_key")
    elif prov == "anthropic":
        app.config["ANTHROPIC_API_KEY"] = lc.get("api_key")


def _sync_llm_config(app: Flask, default_cfg: dict) -> None:
    lc = default_cfg.get("llm_config")
    if not lc:
        return
    prov = _sync_default_provider_settings(lc)
    if not prov:
        return
    _sync_provider_connection_settings(app, prov, lc)


def sync_runtime_state(app: Flask, cfg: dict, changed_keys: set[str] | None = None) -> None:
    changed = set(changed_keys or cfg.keys())

    for key in changed:
        if key in cfg and hasattr(settings, key):
            try:
                setattr(settings, key, cfg.get(key))
                if key.upper() in app.config:
                    app.config[key.upper()] = cfg.get(key)
            except Exception as e:
                app.logger.warning(f"Konnte settings.{key} nicht aktualisieren: {e}")

    provider_url_fields = {
        "ollama_url": "ollama",
        "lmstudio_url": "lmstudio",
        "openai_url": "openai",
        "anthropic_url": "anthropic",
    }
    provider_urls = dict(app.config.get("PROVIDER_URLS", {}) or {})
    provider_urls_changed = False
    for field_name, provider_name in provider_url_fields.items():
        if field_name not in changed or field_name not in cfg:
            continue
        provider_urls[provider_name] = cfg.get(field_name)
        provider_urls_changed = True
        if provider_name == "openai":
            provider_urls["codex"] = cfg.get(field_name)

    if provider_urls_changed:
        app.config["PROVIDER_URLS"] = provider_urls

    if "openai_api_key" in changed and "openai_api_key" in cfg:
        app.config["OPENAI_API_KEY"] = cfg.get("openai_api_key")
    if "anthropic_api_key" in changed and "anthropic_api_key" in cfg:
        app.config["ANTHROPIC_API_KEY"] = cfg.get("anthropic_api_key")

    if "llm_config" in changed and "llm_config" in cfg:
        _sync_llm_config(app, cfg)


def merge_db_config_overrides(default_cfg: dict) -> None:
    try:
        from agent.repository import config_repo
        from agent.services.config_service import unwrap_config

        db_configs = config_repo.get_all()
        reserved_keys = {"status", "message", "code"}
        for cfg in db_configs:
            if cfg.key in reserved_keys:
                continue
            try:
                default_cfg[cfg.key] = unwrap_config(json.loads(cfg.value_json))
            except Exception:
                default_cfg[cfg.key] = cfg.value_json
    except Exception as e:
        logging.warning(f"Konnte Konfiguration nicht aus DB laden: {e}. Nutze Fallback.")


def apply_env_config_overrides(cfg: dict) -> None:
    runtime_cfg = cfg.get("opencode_runtime") if isinstance(cfg.get("opencode_runtime"), dict) else {}
    forced_execution_mode = str(os.environ.get("ANANTA_OPENCODE_EXECUTION_MODE") or "").strip().lower()
    if forced_execution_mode in {"backend", "live_terminal", "interactive_terminal"}:
        runtime_cfg["execution_mode"] = forced_execution_mode
    forced_launch_mode = str(os.environ.get("ANANTA_OPENCODE_INTERACTIVE_LAUNCH_MODE") or "").strip().lower()
    if forced_launch_mode in {"run", "tui"}:
        runtime_cfg["interactive_launch_mode"] = forced_launch_mode
    forced_target_provider = str(os.environ.get("ANANTA_OPENCODE_TARGET_PROVIDER") or "").strip().lower()
    if forced_target_provider in {"ollama", "lmstudio"}:
        runtime_cfg["target_provider"] = forced_target_provider
    elif "target_provider" not in runtime_cfg:
        runtime_cfg["target_provider"] = (
            str(settings.default_provider or "").strip().lower()
            if str(settings.default_provider or "").strip().lower() in {"ollama", "lmstudio"}
            else None
        )
    if runtime_cfg:
        cfg["opencode_runtime"] = runtime_cfg

    parallel_cfg = cfg.get("worker_parallelism") if isinstance(cfg.get("worker_parallelism"), dict) else {}
    if parallel_cfg:
        ollama_cfg = parallel_cfg.get("ollama") if isinstance(parallel_cfg.get("ollama"), dict) else {}
        model_defaults = ollama_cfg.get("model_defaults") if isinstance(ollama_cfg.get("model_defaults"), dict) else {}
        worker_pool = parallel_cfg.get("worker_pool") if isinstance(parallel_cfg.get("worker_pool"), dict) else {}
        worker_defaults = worker_pool.get("worker_defaults") if isinstance(worker_pool.get("worker_defaults"), dict) else {}
        kinds = worker_pool.get("kinds") if isinstance(worker_pool.get("kinds"), dict) else {}
        native_kind = kinds.get("native_ananta_worker") if isinstance(kinds.get("native_ananta_worker"), dict) else {}
        subworkers = native_kind.get("subworkers") if isinstance(native_kind.get("subworkers"), dict) else {}

        if "ANANTA_WORKER_POOL_ENABLED" in os.environ:
            parallel_cfg["enabled"] = str(os.environ.get("ANANTA_WORKER_POOL_ENABLED") or "").strip().lower() in {"1", "true", "yes", "on"}
        if "ANANTA_OLLAMA_MAX_PARALLEL" in os.environ:
            try:
                model_defaults["max_parallel_requests"] = max(1, int(os.environ.get("ANANTA_OLLAMA_MAX_PARALLEL") or 4))
            except Exception:
                pass
        if "ANANTA_WORKER_MAX_PARALLEL_TASKS" in os.environ:
            try:
                worker_defaults["max_parallel_tasks"] = max(1, int(os.environ.get("ANANTA_WORKER_MAX_PARALLEL_TASKS") or 4))
                native_kind["max_parallel_tasks_per_container"] = worker_defaults["max_parallel_tasks"]
            except Exception:
                pass
        if "ANANTA_SUBWORKER_MAX_CHILDREN" in os.environ:
            try:
                subworkers["max_children_per_parent"] = max(1, int(os.environ.get("ANANTA_SUBWORKER_MAX_CHILDREN") or 4))
            except Exception:
                pass

        if model_defaults:
            ollama_cfg["model_defaults"] = model_defaults
        if subworkers:
            native_kind["subworkers"] = subworkers
        if native_kind:
            kinds["native_ananta_worker"] = native_kind
        if kinds:
            worker_pool["kinds"] = kinds
        if worker_defaults:
            worker_pool["worker_defaults"] = worker_defaults
        if worker_pool:
            parallel_cfg["worker_pool"] = worker_pool
        if ollama_cfg:
            parallel_cfg["ollama"] = ollama_cfg
        cfg["worker_parallelism"] = parallel_cfg

    evolution_cfg = cfg.get("evolution") if isinstance(cfg.get("evolution"), dict) else {}
    provider_overrides = evolution_cfg.get("provider_overrides")
    if not isinstance(provider_overrides, dict):
        provider_overrides = {}
    evolver_cfg = dict(provider_overrides.get("evolver") or {})
    evolver_headers = getattr(settings, "evolver_headers", None)
    parsed_evolver_headers = {}
    if evolver_headers:
        try:
            raw_headers = json.loads(evolver_headers)
            if isinstance(raw_headers, dict):
                parsed_evolver_headers = {str(key): str(value) for key, value in raw_headers.items()}
        except Exception:
            parsed_evolver_headers = {}
    allowed_hosts = [
        item.strip()
        for item in str(getattr(settings, "evolver_allowed_hosts", "") or "").split(",")
        if item.strip()
    ]
    env_to_key = {
        "EVOLVER_ENABLED": ("enabled", bool(getattr(settings, "evolver_enabled", False))),
        "EVOLVER_BASE_URL": ("base_url", getattr(settings, "evolver_base_url", None)),
        "EVOLVER_ANALYZE_PATH": ("analyze_path", getattr(settings, "evolver_analyze_path", "/evolution/analyze")),
        "EVOLVER_HEALTH_PATH": ("health_path", getattr(settings, "evolver_health_path", None)),
        "EVOLVER_TIMEOUT_SECONDS": (
            "timeout_seconds",
            float(getattr(settings, "evolver_timeout_seconds", 30.0) or 30.0),
        ),
        "EVOLVER_CONNECT_TIMEOUT_SECONDS": (
            "connect_timeout_seconds",
            getattr(settings, "evolver_connect_timeout_seconds", None),
        ),
        "EVOLVER_READ_TIMEOUT_SECONDS": (
            "read_timeout_seconds",
            getattr(settings, "evolver_read_timeout_seconds", None),
        ),
        "EVOLVER_MAX_RESPONSE_BYTES": (
            "max_response_bytes",
            int(getattr(settings, "evolver_max_response_bytes", 1048576) or 1048576),
        ),
        "EVOLVER_RETRY_COUNT": ("retry_count", int(getattr(settings, "evolver_retry_count", 0) or 0)),
        "EVOLVER_RETRY_BACKOFF_SECONDS": (
            "retry_backoff_seconds",
            float(getattr(settings, "evolver_retry_backoff_seconds", 0.0) or 0.0),
        ),
        "EVOLVER_BEARER_TOKEN": ("bearer_token", getattr(settings, "evolver_bearer_token", None)),
        "EVOLVER_HEADERS": ("headers", parsed_evolver_headers),
        "EVOLVER_ALLOWED_HOSTS": ("allowed_hosts", allowed_hosts),
        "EVOLVER_FORCE_ANALYZE_ONLY": (
            "force_analyze_only",
            bool(getattr(settings, "evolver_force_analyze_only", True)),
        ),
        "EVOLVER_DEFAULT": ("default", bool(getattr(settings, "evolver_default", False))),
        "EVOLVER_VERSION": ("version", getattr(settings, "evolver_version", "unknown")),
    }
    for env_name, (key, value) in env_to_key.items():
        if env_name in os.environ:
            evolver_cfg[key] = value
    provider_overrides["evolver"] = evolver_cfg
    evolution_cfg["provider_overrides"] = provider_overrides
    if evolver_cfg["enabled"] and evolver_cfg["default"]:
        evolution_cfg["default_provider"] = evolver_cfg.get("provider_name") or "evolver"
    cfg["evolution"] = evolution_cfg
