from __future__ import annotations

import os
from typing import Any

from worker.core.hermes_adapter_config import HermesAdapterConfig, HermesModelSelectionPolicy


OPENROUTER_HERMES_DEFAULT_CONFIG_PATH = "config/workers/hermes.openrouter.yaml"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
OPENROUTER_PRIMARY_MODEL = "google/gemini-2.5-flash"
OPENROUTER_CODING_FALLBACK_MODEL = "deepseek/deepseek-v4-flash"
OPENROUTER_CHEAP_FALLBACK_MODEL = "google/gemini-2.5-flash-lite"


def build_openrouter_hermes_adapter_config(
    *,
    enabled: bool = True,
    feature_flag_enabled: bool = True,
    overrides: dict[str, Any] | None = None,
) -> HermesAdapterConfig:
    """Return the default paid/cheap OpenRouter config for the Hermes adapter.

    This is intentionally not a `:free` routing profile. Hermes creates many small
    agentic requests; OpenRouter free models tend to fail with rate limits and
    disappearing endpoints. The config remains explicit and cloud-gated.
    """
    data: dict[str, Any] = {
        "enabled": enabled,
        "feature_flag_enabled": feature_flag_enabled,
        "base_url": os.getenv("HERMES_BASE_URL", OPENROUTER_BASE_URL),
        "api_key_env": os.getenv("HERMES_API_KEY_ENV", "OPENROUTER_API_KEY"),
        "default_model": os.getenv("HERMES_MODEL", OPENROUTER_PRIMARY_MODEL),
        "timeout_seconds": float(os.getenv("HERMES_TIMEOUT_SECONDS", "180")),
        "max_retries": int(os.getenv("HERMES_MAX_RETRIES", "1")),
        "allowed_task_kinds": ["plan_only", "review", "summarize", "patch_propose", "research_limited"],
        "blocked_task_kinds": ["patch_apply", "command_execute", "shell_execute", "shell_execution", "service_mutation", "config_mutation"],
        "cloud_allowed": _env_bool("HERMES_CLOUD_ALLOWED", True),
        "max_context_chars": int(os.getenv("HERMES_MAX_CONTEXT_CHARS", "120000")),
        "strict_json_required": True,
        "parse_retry_enabled": True,
        "default_temperature": float(os.getenv("HERMES_TEMPERATURE", "0.1")),
        "max_output_tokens": int(os.getenv("HERMES_MAX_OUTPUT_TOKENS", "4096")),
        "blocked_models": [":free"],
        "task_kind_models": {
            "plan_only": OPENROUTER_PRIMARY_MODEL,
            "review": OPENROUTER_CODING_FALLBACK_MODEL,
            "analysis": OPENROUTER_CODING_FALLBACK_MODEL,
            "summarize": OPENROUTER_CHEAP_FALLBACK_MODEL,
            "patch_propose": OPENROUTER_CODING_FALLBACK_MODEL,
            "research_limited": OPENROUTER_PRIMARY_MODEL,
        },
        "fallback_free_models": [
            OPENROUTER_PRIMARY_MODEL,
            OPENROUTER_CODING_FALLBACK_MODEL,
            OPENROUTER_CHEAP_FALLBACK_MODEL,
        ],
        "model_selection_policy": HermesModelSelectionPolicy(
            prefer_task_specific_model=True,
            require_free_model_suffix=False,
            allow_fallback_on_unavailable=True,
            reject_blocked_models=True,
            reject_mutation_tasks_for_hermes=True,
            allow_candidate_roles=True,
            allowed_task_kinds_for_hermes=["plan_only", "review", "summarize", "patch_propose", "research_limited"],
            blocked_task_kinds=["patch_apply", "command_execute", "shell_execute", "shell_execution", "service_mutation", "config_mutation"],
        ),
    }
    if overrides:
        data.update({key: value for key, value in overrides.items() if value is not None})
    return HermesAdapterConfig(**data)


def build_hermes_adapter_config_from_agent_config(agent_cfg: dict[str, Any] | None) -> HermesAdapterConfig:
    """Resolve Hermes config from AGENT_CONFIG with OpenRouter defaults.

    If `hermes_worker_adapter` is present, it may override any default field.
    If `worker.type == hermes` or `HERMES_WORKER=1`, Hermes is enabled directly.
    """
    cfg = dict(agent_cfg or {})
    hermes_cfg = cfg.get("hermes_worker_adapter") if isinstance(cfg.get("hermes_worker_adapter"), dict) else {}
    worker_cfg = cfg.get("worker") if isinstance(cfg.get("worker"), dict) else {}
    worker_type = str(worker_cfg.get("type") or os.getenv("WORKER_TYPE") or "").strip().lower()
    explicit_hermes_worker = worker_type == "hermes" or _env_bool("HERMES_WORKER", False)
    feature_flags = cfg.get("feature_flags") if isinstance(cfg.get("feature_flags"), dict) else {}
    enabled = bool(hermes_cfg.get("enabled", explicit_hermes_worker))
    feature_enabled = bool(
        hermes_cfg.get(
            "feature_flag_enabled",
            feature_flags.get("enable_hermes_worker_adapter", explicit_hermes_worker),
        )
    )
    overrides = dict(hermes_cfg)
    overrides.pop("enabled", None)
    overrides.pop("feature_flag_enabled", None)
    return build_openrouter_hermes_adapter_config(
        enabled=enabled,
        feature_flag_enabled=feature_enabled,
        overrides=overrides,
    )


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}
