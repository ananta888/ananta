"""Worker/CLI runtime normalization and block-merge steps of the POST /config update.

Every step keeps the validation rules and error messages of the former
monolithic ``set_config`` handler; see :mod:`config_update_contract`.
"""

from __future__ import annotations

from typing import Callable

from flask import current_app

from agent.local_llm_backends import get_local_openai_backends
from agent.routes.config import shared
from agent.routes.config.config_update_contract import (
    ConfigUpdateContext,
    ConfigUpdateRejected,
    ConfigUpdateStep,
    require_dict,
)
from agent.services.context_bundle_service import normalize_context_bundle_policy_config
from agent.services.worker_execution_profile_service import normalize_worker_execution_profile

_CLI_SESSION_BACKENDS = {
    "sgpt",
    "codex",
    "claude_code",
    "opencode",
    "aider",
    "mistral_code",
    "qwen_code",
    "gemini_cli",
    "copilot_cli",
    "cline",
    "kilo_code",
    "deerflow",
    "ananta_research",
}
_DEFAULT_RISK_CANDIDATES = ["low", "medium", "high", "critical"]
_NESTED_MERGE_BLOCKS = (
    "llm_config",
    "research_backend",
    "opencode_runtime",
    "aider_cli",
    "worker_runtime",
    "planning_policy",
    "specialized_worker_profiles",
    "ml_intern_spike",
)


def merge_nested_config_block(current_cfg: dict, new_cfg: dict, key: str) -> dict:
    if key in new_cfg and isinstance(new_cfg[key], dict):
        merged = (current_cfg.get(key, {}) or {}).copy()
        merged.update(new_cfg[key])
        new_cfg = {**new_cfg, key: merged}
    return new_cfg


def configured_coding_target_providers(current_cfg: dict, new_cfg: dict) -> set[str]:
    candidate_cfg = dict(current_cfg or {})
    for key in ("default_provider", "default_model", "local_openai_backends", "remote_ananta_backends"):
        if key in new_cfg:
            candidate_cfg[key] = new_cfg[key]
    provider_urls = dict(current_app.config.get("PROVIDER_URLS", {}) or {})
    providers = {
        str(entry.get("provider") or "").strip().lower()
        for entry in get_local_openai_backends(
            agent_cfg=candidate_cfg,
            provider_urls=provider_urls,
            default_provider=str(candidate_cfg.get("default_provider") or "").strip().lower() or None,
            default_model=str(candidate_cfg.get("default_model") or ""),
        )
        if str(entry.get("provider") or "").strip()
    }
    providers.update({"ollama", "lmstudio"})
    return providers


def _require_allowed_target_provider(raw_provider, ctx: ConfigUpdateContext, new_cfg: dict, message: str):
    target_provider = str(raw_provider or "").strip().lower() or None
    if target_provider is not None and target_provider not in configured_coding_target_providers(
        ctx.current_cfg, new_cfg
    ):
        raise ConfigUpdateRejected(message)
    return target_provider


def _bounded_int_or_reject(mode_cfg: dict) -> tuple[int, int]:
    try:
        return int(mode_cfg.get("max_turns_per_session", 40)), int(mode_cfg.get("max_sessions", 200))
    except (TypeError, ValueError) as exc:
        raise ConfigUpdateRejected("invalid_cli_session_limits") from exc


def normalize_cli_session_mode(new_cfg: dict, ctx: ConfigUpdateContext) -> dict:
    if "cli_session_mode" not in new_cfg:
        return new_cfg
    mode_cfg = require_dict(new_cfg.get("cli_session_mode"), "invalid_cli_session_mode")
    backends = [
        str(item or "").strip().lower()
        for item in list(mode_cfg.get("stateful_backends") or [])
        if str(item or "").strip()
    ]
    if any(backend not in _CLI_SESSION_BACKENDS for backend in backends):
        raise ConfigUpdateRejected("invalid_cli_session_backend")
    max_turns, max_sessions = _bounded_int_or_reject(mode_cfg)
    if max_turns < 1 or max_turns > 200:
        raise ConfigUpdateRejected("invalid_cli_session_max_turns")
    if max_sessions < 1 or max_sessions > 2000:
        raise ConfigUpdateRejected("invalid_cli_session_max_sessions")
    reuse_scope = str(mode_cfg.get("reuse_scope") or "task").strip().lower()
    if reuse_scope not in {"task", "role"}:
        raise ConfigUpdateRejected("invalid_cli_session_reuse_scope")
    new_cfg["cli_session_mode"] = {
        "enabled": bool(mode_cfg.get("enabled", False)),
        "stateful_backends": backends or ["opencode", "codex"],
        "max_turns_per_session": max_turns,
        "max_sessions": max_sessions,
        "allow_task_scoped_auto_session": bool(mode_cfg.get("allow_task_scoped_auto_session", True)),
        "reuse_scope": reuse_scope,
        "native_opencode_sessions": bool(mode_cfg.get("native_opencode_sessions", False)),
    }
    return new_cfg


def _choice(raw, default: str, allowed: set[str], message: str) -> str:
    value = str(raw or default).strip().lower()
    if value not in allowed:
        raise ConfigUpdateRejected(message)
    return value


def normalize_opencode_runtime(new_cfg: dict, ctx: ConfigUpdateContext) -> dict:
    if "opencode_runtime" not in new_cfg:
        return new_cfg
    runtime_cfg = require_dict(new_cfg.get("opencode_runtime"), "invalid_opencode_runtime")
    tool_mode = _choice(
        runtime_cfg.get("tool_mode"), "full", {"full", "readonly", "toolless"}, "invalid_opencode_tool_mode"
    )
    execution_mode = _choice(
        runtime_cfg.get("execution_mode"),
        "live_terminal",
        {"backend", "live_terminal", "interactive_terminal"},
        "invalid_opencode_execution_mode",
    )
    interactive_launch_mode = _choice(
        runtime_cfg.get("interactive_launch_mode"),
        "run",
        {"run", "tui"},
        "invalid_opencode_interactive_launch_mode",
    )
    target_provider = _require_allowed_target_provider(
        runtime_cfg.get("target_provider"), ctx, new_cfg, "invalid_opencode_target_provider"
    )
    target_profile = str(runtime_cfg.get("target_profile") or "").strip() or None
    target_model = str(runtime_cfg.get("target_model") or runtime_cfg.get("model") or "").strip() or None
    new_cfg["opencode_runtime"] = {
        "tool_mode": tool_mode,
        "execution_mode": execution_mode,
        "interactive_launch_mode": interactive_launch_mode,
        "target_profile": target_profile,
        "target_provider": target_provider,
        "target_model": target_model,
    }
    return new_cfg


def normalize_aider_cli(new_cfg: dict, ctx: ConfigUpdateContext) -> dict:
    if "aider_cli" not in new_cfg:
        return new_cfg
    aider_cfg = require_dict(new_cfg.get("aider_cli"), "invalid_aider_cli")
    target_provider = _require_allowed_target_provider(
        aider_cfg.get("target_provider"), ctx, new_cfg, "invalid_aider_target_provider"
    )
    target_model = str(aider_cfg.get("model") or aider_cfg.get("default_model") or "").strip() or None
    if target_model and len(target_model) > 300:
        raise ConfigUpdateRejected("invalid_aider_model")
    api_key_profile = str(aider_cfg.get("api_key_profile") or "").strip() or None
    if api_key_profile and (len(api_key_profile) > 100 or any(ord(char) < 32 for char in api_key_profile)):
        raise ConfigUpdateRejected("invalid_aider_api_key_profile")
    new_cfg["aider_cli"] = {
        "target_provider": target_provider,
        "model": target_model,
        "api_key_profile": api_key_profile,
    }
    return new_cfg


def _bounded_float(value: object, *, default: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value) if value is not None else default
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _bounded_int(value: object, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value) if value is not None else default
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _optional_dict(value, message: str) -> dict:
    if value is None:
        return {}
    return require_dict(value, message)


def _deduplicated_risk_candidates(risk_cfg: dict) -> list[str]:
    candidates: list[str] = []
    for item in list(risk_cfg.get("candidates") or _DEFAULT_RISK_CANDIDATES):
        normalized = str(item).strip().lower()
        if normalized and normalized not in candidates:
            candidates.append(normalized)
    return candidates or list(_DEFAULT_RISK_CANDIDATES)


def normalize_semantic_output_correction(raw_semantic) -> dict:
    """Normalize ``worker_runtime.semantic_output_correction`` (``None`` disables it)."""

    if raw_semantic is None:
        return {"enabled": False}
    raw_semantic = require_dict(raw_semantic, "invalid_worker_semantic_output_correction")
    provider_cfg = _optional_dict(
        raw_semantic.get("embedding_provider"), "invalid_worker_semantic_output_embedding_provider"
    )
    fields_cfg = _optional_dict(raw_semantic.get("fields"), "invalid_worker_semantic_output_fields")
    risk_cfg = _optional_dict(fields_cfg.get("risk_classification"), "invalid_worker_semantic_output_risk_field")
    return {
        "enabled": bool(raw_semantic.get("enabled", False)),
        "similarity_threshold": _bounded_float(
            raw_semantic.get("similarity_threshold"), default=0.9, minimum=0.5, maximum=1.0
        ),
        "min_margin": _bounded_float(raw_semantic.get("min_margin"), default=0.03, minimum=0.0, maximum=1.0),
        "lexical_weight": _bounded_float(raw_semantic.get("lexical_weight"), default=0.35, minimum=0.0, maximum=1.0),
        "embedding_provider": {
            "provider": str(provider_cfg.get("provider") or "local").strip().lower() or "local",
            "dimensions": _bounded_int(provider_cfg.get("dimensions"), default=12, minimum=4, maximum=4096),
            "model_version": str(provider_cfg.get("model_version") or "").strip() or None,
            "base_url": str(provider_cfg.get("base_url") or "").strip() or None,
            "api_key": str(provider_cfg.get("api_key") or "").strip() or None,
            "model": str(provider_cfg.get("model") or "").strip() or None,
            "timeout_seconds": _bounded_int(provider_cfg.get("timeout_seconds"), default=20, minimum=1, maximum=120),
        },
        "fields": {
            "risk_classification": {
                "enabled": bool(risk_cfg.get("enabled", True)),
                "candidates": _deduplicated_risk_candidates(risk_cfg),
            }
        },
    }


def _normalize_todo_contract(raw_todo_contract) -> dict:
    if raw_todo_contract is None:
        return shared.normalize_worker_todo_contract_config({"enabled": False})
    return shared.normalize_worker_todo_contract_config(require_dict(raw_todo_contract, "invalid_worker_todo_contract"))


def normalize_worker_runtime(new_cfg: dict, ctx: ConfigUpdateContext) -> dict:
    if "worker_runtime" not in new_cfg:
        return new_cfg
    worker_runtime_cfg = require_dict(new_cfg.get("worker_runtime"), "invalid_worker_runtime")
    workspace_root = worker_runtime_cfg.get("workspace_root")
    workspace_root = str(workspace_root).strip() if workspace_root is not None else None
    workspace_reuse_mode = (
        str(worker_runtime_cfg.get("workspace_reuse_mode") or "goal_worker").strip().lower() or "goal_worker"
    )
    if workspace_reuse_mode not in {"task", "goal_worker"}:
        raise ConfigUpdateRejected("invalid_worker_workspace_reuse_mode")
    current_worker_runtime = ctx.current_cfg.get("worker_runtime")
    current_worker_runtime = current_worker_runtime if isinstance(current_worker_runtime, dict) else {}
    default_execution_profile = normalize_worker_execution_profile(
        worker_runtime_cfg.get("default_execution_profile")
        or current_worker_runtime.get("default_execution_profile")
        or "balanced"
    )
    semantic_output_correction = None
    todo_contract = None
    if "semantic_output_correction" in worker_runtime_cfg:
        semantic_output_correction = normalize_semantic_output_correction(
            worker_runtime_cfg.get("semantic_output_correction")
        )
    if "todo_contract" in worker_runtime_cfg:
        todo_contract = _normalize_todo_contract(worker_runtime_cfg.get("todo_contract"))
    new_cfg["worker_runtime"] = {
        "workspace_root": workspace_root or None,
        "workspace_reuse_mode": workspace_reuse_mode,
        "default_execution_profile": default_execution_profile,
    }
    if semantic_output_correction is not None:
        new_cfg["worker_runtime"]["semantic_output_correction"] = semantic_output_correction
    if todo_contract is not None:
        new_cfg["worker_runtime"]["todo_contract"] = todo_contract
    return new_cfg


def normalize_planning_policy_and_model_overrides(new_cfg: dict, ctx: ConfigUpdateContext) -> dict:
    if "planning_policy" in new_cfg:
        planning_policy_cfg = require_dict(new_cfg.get("planning_policy"), "invalid_planning_policy")
        new_cfg["planning_policy"] = shared.normalize_planning_policy_config(planning_policy_cfg)
    for key in ("role_model_overrides", "template_model_overrides", "task_kind_model_overrides"):
        if key in new_cfg:
            override_cfg = require_dict(new_cfg.get(key), f"invalid_{key}")
            new_cfg[key] = shared.normalize_model_override_map(override_cfg)
    return new_cfg


def merge_nested_blocks(new_cfg: dict, ctx: ConfigUpdateContext) -> dict:
    for key in _NESTED_MERGE_BLOCKS:
        new_cfg = merge_nested_config_block(ctx.current_cfg, new_cfg, key)
    return new_cfg


def merged_block_step(
    key: str,
    normalizer: Callable[[dict], dict],
    *,
    invalid_message: str | None = None,
) -> ConfigUpdateStep:
    """Step that merges ``new_cfg[key]`` over the current block and normalizes it.

    Without ``invalid_message`` a non-dict value is left untouched; with it the
    update is rejected.
    """

    def step(new_cfg: dict, ctx: ConfigUpdateContext) -> dict:
        if key not in new_cfg:
            return new_cfg
        block = new_cfg[key]
        if not isinstance(block, dict):
            if invalid_message is None:
                return new_cfg
            raise ConfigUpdateRejected(invalid_message)
        merged = (ctx.current_cfg.get(key, {}) or {}).copy()
        merged.update(block)
        return {**new_cfg, key: normalizer(merged)}

    step.__name__ = f"merge_{key}"
    return step


def normalize_doom_loop_policy(new_cfg: dict, ctx: ConfigUpdateContext) -> dict:
    if "doom_loop_policy" not in new_cfg:
        return new_cfg
    doom_loop_cfg = require_dict(new_cfg.get("doom_loop_policy"), "invalid_doom_loop_policy")
    merged = (ctx.current_cfg.get("doom_loop_policy", {}) or {}).copy()
    merged.update({key: value for key, value in doom_loop_cfg.items() if key != "severity_actions"})
    if isinstance(doom_loop_cfg.get("severity_actions"), dict):
        existing = merged.get("severity_actions")
        existing_severity_actions = dict(existing or {}) if isinstance(existing, dict) else {}
        existing_severity_actions.update(doom_loop_cfg.get("severity_actions") or {})
        merged["severity_actions"] = existing_severity_actions
    elif "severity_actions" in doom_loop_cfg:
        merged["severity_actions"] = doom_loop_cfg.get("severity_actions")
    return {**new_cfg, "doom_loop_policy": shared.normalize_doom_loop_policy_config(merged)}


def _merge_specialized_profiles(merged: dict, requested_profiles) -> None:
    if not isinstance(requested_profiles, dict):
        return
    if not isinstance(merged.get("profiles"), dict):
        merged["profiles"] = dict(requested_profiles or {})
        return
    merged_profiles = dict(merged.get("profiles") or {})
    for profile_id, profile_cfg in (requested_profiles or {}).items():
        if not isinstance(profile_cfg, dict):
            continue
        existing_profile = merged_profiles.get(profile_id)
        previous_profile = existing_profile if isinstance(existing_profile, dict) else {}
        merged_profiles[profile_id] = {**previous_profile, **profile_cfg}
    merged["profiles"] = merged_profiles


def normalize_specialized_worker_profiles(new_cfg: dict, ctx: ConfigUpdateContext) -> dict:
    if "specialized_worker_profiles" not in new_cfg:
        return new_cfg
    specialized_cfg = require_dict(new_cfg.get("specialized_worker_profiles"), "invalid_specialized_worker_profiles")
    merged = (ctx.current_cfg.get("specialized_worker_profiles", {}) or {}).copy()
    merged.update({key: value for key, value in specialized_cfg.items() if key != "profiles"})
    _merge_specialized_profiles(merged, specialized_cfg.get("profiles"))
    return {
        **new_cfg,
        "specialized_worker_profiles": shared.normalize_specialized_worker_profiles_config(merged),
    }


MERGED_BLOCK_STEPS: tuple[ConfigUpdateStep, ...] = (
    merged_block_step("hub_copilot", shared.normalize_hub_copilot_config),
    merged_block_step("context_bundle_policy", normalize_context_bundle_policy_config),
    merged_block_step("artifact_flow", shared.normalize_artifact_flow_config),
    merged_block_step("planning_policy", shared.normalize_planning_policy_config),
    normalize_doom_loop_policy,
    merged_block_step(
        "unified_approval_policy",
        shared.normalize_unified_approval_policy_config,
        invalid_message="invalid_unified_approval_policy",
    ),
    merged_block_step("mutation_gate", shared.normalize_mutation_gate_config, invalid_message="invalid_mutation_gate"),
    normalize_specialized_worker_profiles,
    merged_block_step(
        "ml_intern_spike",
        shared.normalize_ml_intern_spike_config,
        invalid_message="invalid_ml_intern_spike",
    ),
)
