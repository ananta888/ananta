import logging
import os
from typing import Any
from flask import Flask
from agent.config import settings
from agent.runtime_profiles import runtime_profile_catalog

def _provider_alias(provider: str | None) -> str:
    value = str(provider or "").strip().lower()
    return "openai" if value == "codex" else value

def build_default_agent_config() -> dict:
    return {
        "default_provider": settings.default_provider,
        "default_model": settings.default_model,
        "provider": settings.default_provider,
        "model": settings.default_model,
        "llm_config": {
            "provider": settings.default_provider,
            "model": settings.default_model,
            "base_url": settings.lmstudio_url if settings.default_provider == "lmstudio" else None,
            "lmstudio_api_mode": settings.lmstudio_api_mode,
        },
        "max_summary_length": 500,
        "quality_gates": {
            "enabled": True,
            "autopilot_enforce": True,
            "coding_keywords": ["code", "implement", "fix", "refactor", "bug", "test", "feature", "endpoint"],
            "required_output_markers_for_coding": ["test", "pytest", "passed", "success", "lint", "ok"],
            "min_output_chars": 8,
        },
        "autonomous_guardrails": {
            "enabled": True,
            "max_runtime_seconds": 21600,
            "max_ticks_total": 5000,
            "max_dispatched_total": 50000,
        },
        "llm_tool_guardrails": {
            "enabled": True,
            "max_tool_calls_per_request": 5,
            "max_external_calls_per_request": 2,
            "max_estimated_cost_units_per_request": 20,
            "max_tokens_per_request": 6000,
            "chars_per_token_estimate": 4,
            "class_limits": {"read": 5, "write": 2, "admin": 1},
            "class_cost_units": {"read": 1, "write": 5, "admin": 8, "unknown": 3},
            "external_classes": ["write", "admin"],
            "tool_classes": {
                "list_teams": "read",
                "list_roles": "read",
                "list_agents": "read",
                "list_templates": "read",
                "analyze_logs": "read",
                "read_agent_logs": "read",
                "create_team": "write",
                "assign_role": "write",
                "ensure_team_templates": "write",
                "create_template": "write",
                "update_template": "write",
                "delete_template": "write",
                "upsert_team_type": "write",
                "delete_team_type": "write",
                "upsert_role": "write",
                "delete_role": "write",
                "link_role_to_team_type": "write",
                "unlink_role_from_team_type": "write",
                "set_role_template_mapping": "write",
                "upsert_team": "write",
                "delete_team": "write",
                "activate_team": "write",
                "configure_auto_planner": "admin",
                "configure_triggers": "admin",
                "set_autopilot_state": "admin",
                "update_config": "admin",
            },
        },
        "autonomous_resilience": {
            "retry_attempts": 2,
            "retry_backoff_seconds": 0.2,
            "retry_max_backoff_seconds": 5.0,
            "retry_jitter_factor": 0.2,
            "circuit_breaker_threshold": 3,
            "circuit_breaker_open_seconds": 30,
        },
        "autopilot_strategy_max_attempts": 3,
        "autopilot_strategy_retry_delay_seconds": 20,
        "autopilot_strategy_fallback_models": [],
        "autopilot_strategy_temperature_profiles": [0.2, 0.5, 0.8],
        "adaptive_model_routing_enabled": True,
        "adaptive_model_routing_min_samples": 3,
        "adaptive_model_routing_top_k": 3,
        "execution_fallback_policy": {
            "allow_hub_worker_fallback": True,
            "escalate_on_fallback_block": True,
            "fallback_block_status": "blocked",
        },
        "exposure_policy": {
            "openai_compat": {
                "enabled": True,
                "allow_agent_auth": True,
                "allow_user_auth": True,
                "require_admin_for_user_auth": True,
                "allow_files_api": True,
                "emit_audit_events": True,
                "instance_id": None,
                "max_hops": 3,
            },
            "mcp": {
                "enabled": False,
                "allow_agent_auth": False,
                "allow_user_auth": False,
                "require_admin_for_user_auth": True,
                "emit_audit_events": True,
            },
        },
        "goal_plan_limits": {
            "max_plan_nodes": 8,
            "max_plan_depth": 8,
        },
        "task_kind_execution_policies": {
            "coding": {
                "command_timeout": 90,
                "command_retries": 1,
                "command_retry_delay": 2,
                "command_retry_strategy": "exponential",
                "command_max_retry_delay": 15,
            },
            "analysis": {
                "command_timeout": 60,
                "command_retries": 0,
                "command_retry_delay": 1,
            },
            "doc": {
                "command_timeout": 45,
                "command_retries": 0,
                "command_retry_delay": 1,
            },
            "ops": {
                "command_timeout": 120,
                "command_retries": 2,
                "command_retry_delay": 3,
                "command_retry_strategy": "exponential",
                "command_max_retry_delay": 20,
            },
            "research": {
                "command_timeout": 180,
                "command_retries": 1,
                "command_retry_delay": 2,
                "command_retry_strategy": "exponential",
                "command_max_retry_delay": 20,
            },
        },
        "llm_pricing": {
            "default": {"cost_per_1k_tokens": 0.0},
        },
        "review_policy": {
            "enabled": True,
            "policy_version": "review-v2",
            "research_backends": ["deerflow", "ananta_research"],
            "task_kinds": ["research"],
            "min_risk_level_for_review": "high",
            "terminal_risk_level": "high",
            "file_access_risk_level": "medium",
        },
        "execution_risk_policy": {
            "enabled": True,
            "default_action": "deny",
            "deny_risk_levels": ["high", "critical"],
            "review_risk_levels": ["medium", "high", "critical"],
            "task_scoped_only": True,
            "require_terminal_capability_for_command": True,
            "terminal_capability_name": "terminal",
        },
        "autopilot_security_policies": {
            "safe": {
                "max_concurrency_cap": 1,
                "execute_timeout": 45,
                "execute_retries": 0,
                "allowed_tool_classes": ["read"],
            },
            "balanced": {
                "max_concurrency_cap": 2,
                "execute_timeout": 60,
                "execute_retries": 1,
                "allowed_tool_classes": ["read", "write"],
            },
            "aggressive": {
                "max_concurrency_cap": 4,
                "execute_timeout": 120,
                "execute_retries": 2,
                "allowed_tool_classes": ["read", "write", "admin", "unknown"],
            },
        },
        "sgpt_routing": {
            "policy_version": "v2",
            "default_backend": "sgpt",
            "task_kind_backend": {
                "coding": "aider",
                "analysis": "sgpt",
                "doc": "sgpt",
                "ops": "opencode",
                "research": "deerflow",
            },
            "research_capability_backend": {},
        },
        "codex_cli": {
            "base_url": None,
            "api_key_profile": None,
            "prefer_lmstudio": True,
        },
        "cli_session_mode": {
            "enabled": False,
            "stateful_backends": ["opencode", "codex"],
            "max_turns_per_session": 40,
            "max_sessions": 200,
            "allow_task_scoped_auto_session": True,
        },
        "research_backend": {
            "provider": "deerflow",
            "enabled": False,
            "mode": "cli",
            "command": "python main.py {prompt}",
            "working_dir": None,
            "timeout_seconds": 900,
            "result_format": "markdown",
            "docker_binary": "docker",
            "sandbox_image": None,
            "sandbox_network": "none",
            "sandbox_workdir": "/workspace",
            "sandbox_mount_repo": True,
            "sandbox_read_only": True,
            "sandbox_tmp_dir": "/tmp/ananta-research",
        },
        "runtime_profile": "local-dev",
        "runtime_profile_catalog": runtime_profile_catalog(),
    }

def merge_db_config_overrides(default_cfg: dict) -> None:
    try:
        import json
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

def _sync_llm_config(app: Flask, default_cfg: dict) -> None:
    lc = default_cfg.get("llm_config")
    if not lc:
        return
    prov = _sync_default_provider_settings(lc)
    if not prov:
        return
    _sync_provider_connection_settings(app, prov, lc)
