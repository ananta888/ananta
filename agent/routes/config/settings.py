from __future__ import annotations

import json

from flask import Blueprint, current_app, request

from agent.auth import admin_required, check_auth
from agent.common.api_envelope import unwrap_api_envelope
from agent.common.audit import log_audit
from agent.common.errors import api_response
from agent.config_defaults import sync_runtime_state
from agent.db_models import ConfigDB
from agent.governance_modes import governance_mode_catalog
from agent.governance_modes import resolve_governance_mode
from agent.runtime_profiles import resolve_runtime_profile, runtime_profile_catalog
from agent.services.context_bundle_service import normalize_context_bundle_policy_config
from agent.services.exposure_policy_service import get_exposure_policy_service
from agent.services.governance_profile_service import build_effective_policy_profile
from agent.services.platform_governance_service import get_platform_governance_service
from agent.services.remote_federation_policy_service import get_remote_federation_policy_service
from agent.services.result_memory_service import normalize_result_memory_policy
from agent.services.routing_decision_service import get_routing_decision_service
from agent.services.repository_registry import get_repository_registry

from . import shared

settings_bp = Blueprint("config_settings", __name__)


def unwrap_config(data):
    """Rekursives Entpacken von API-Response-Wrappern in der Config."""
    if not isinstance(data, dict):
        return data
    if "data" in data and ("status" in data or "code" in data):
        nested = data.get("data")
        if isinstance(nested, dict):
            unwrapped = unwrap_api_envelope(data)
            return {key: unwrap_config(value) for key, value in unwrapped.items()}
        return unwrap_config(nested)
    return {key: unwrap_config(value) for key, value in data.items()}


def _merge_nested_config_block(current_cfg: dict, new_cfg: dict, key: str) -> dict:
    if key in new_cfg and isinstance(new_cfg[key], dict):
        merged = (current_cfg.get(key, {}) or {}).copy()
        merged.update(new_cfg[key])
        new_cfg = {**new_cfg, key: merged}
    return new_cfg


@settings_bp.route("/config", methods=["GET"])
@check_auth
def get_config():
    cfg = dict(current_app.config.get("AGENT_CONFIG", {}) or {})
    cfg["runtime_profile_effective"] = resolve_runtime_profile(cfg)
    cfg["governance_mode_effective"] = resolve_governance_mode(cfg)
    cfg["effective_policy_profile"] = build_effective_policy_profile(cfg)
    return api_response(data=cfg)


@settings_bp.route("/config", methods=["POST"])
@admin_required
def set_config():
    new_cfg = request.get_json()
    if not isinstance(new_cfg, dict):
        return api_response(status="error", message="invalid_json", code=400)

    new_cfg = unwrap_config(new_cfg)
    current_cfg = current_app.config.get("AGENT_CONFIG", {})
    if "runtime_profile" in new_cfg:
        requested_profile = str(new_cfg.get("runtime_profile") or "").strip().lower()
        if requested_profile not in runtime_profile_catalog():
            return api_response(status="error", message="invalid_runtime_profile", code=400)
        new_cfg["runtime_profile"] = requested_profile
    if "platform_mode" in new_cfg:
        requested_mode = str(new_cfg.get("platform_mode") or "").strip().lower()
        governance_service = get_platform_governance_service()
        if not governance_service.is_supported_platform_mode(requested_mode):
            return api_response(status="error", message="invalid_platform_mode", code=400)
        new_cfg["platform_mode"] = governance_service.normalize_platform_mode(requested_mode)
    if "governance_mode" in new_cfg:
        requested = str(new_cfg.get("governance_mode") or "").strip().lower()
        if requested and requested not in governance_mode_catalog():
            return api_response(status="error", message="invalid_governance_mode", code=400)
        new_cfg["governance_mode"] = requested or "balanced"
    if "execution_fallback_policy" in new_cfg:
        fallback_cfg = new_cfg.get("execution_fallback_policy")
        if not isinstance(fallback_cfg, dict):
            return api_response(status="error", message="invalid_execution_fallback_policy", code=400)
        normalized_fallback = {
            "allow_hub_worker_fallback": bool(fallback_cfg.get("allow_hub_worker_fallback", True)),
            "escalate_on_fallback_block": bool(fallback_cfg.get("escalate_on_fallback_block", True)),
            "fallback_block_status": str(fallback_cfg.get("fallback_block_status") or "blocked").strip().lower() or "blocked",
        }
        if normalized_fallback["fallback_block_status"] not in {"blocked", "failed", "todo"}:
            return api_response(status="error", message="invalid_fallback_block_status", code=400)
        new_cfg["execution_fallback_policy"] = normalized_fallback
    if "routing_fallback_policy" in new_cfg:
        routing_fallback_cfg = new_cfg.get("routing_fallback_policy")
        if not isinstance(routing_fallback_cfg, dict):
            return api_response(status="error", message="invalid_routing_fallback_policy", code=400)
        if "fallback_order" in routing_fallback_cfg and not isinstance(routing_fallback_cfg.get("fallback_order"), list):
            return api_response(status="error", message="invalid_routing_fallback_order", code=400)
        new_cfg["routing_fallback_policy"] = get_routing_decision_service().normalize_fallback_policy(routing_fallback_cfg)
    if "result_memory_policy" in new_cfg:
        memory_cfg = new_cfg.get("result_memory_policy")
        if not isinstance(memory_cfg, dict):
            return api_response(status="error", message="invalid_result_memory_policy", code=400)
        new_cfg["result_memory_policy"] = normalize_result_memory_policy(memory_cfg)
    if "remote_federation_policy" in new_cfg:
        federation_cfg = new_cfg.get("remote_federation_policy")
        if not isinstance(federation_cfg, dict):
            return api_response(status="error", message="invalid_remote_federation_policy", code=400)
        if "allowed_operations" in federation_cfg and not isinstance(federation_cfg.get("allowed_operations"), list):
            return api_response(status="error", message="invalid_remote_federation_operations", code=400)
        new_cfg["remote_federation_policy"] = get_remote_federation_policy_service().normalize_policy(federation_cfg)
    if "autonomous_resilience" in new_cfg:
        resilience_cfg = new_cfg.get("autonomous_resilience")
        if not isinstance(resilience_cfg, dict):
            return api_response(status="error", message="invalid_autonomous_resilience", code=400)
        strategy = str(resilience_cfg.get("retry_backoff_strategy") or "exponential").strip().lower()
        if strategy not in {"constant", "exponential"}:
            return api_response(status="error", message="invalid_retry_backoff_strategy", code=400)
    if "exposure_policy" in new_cfg:
        exposure_cfg = new_cfg.get("exposure_policy")
        if not isinstance(exposure_cfg, dict):
            return api_response(status="error", message="invalid_exposure_policy", code=400)
        openai_cfg = exposure_cfg.get("openai_compat", {})
        mcp_cfg = exposure_cfg.get("mcp", {})
        remote_hubs_cfg = exposure_cfg.get("remote_hubs", {})
        if openai_cfg and not isinstance(openai_cfg, dict):
            return api_response(status="error", message="invalid_openai_compat_exposure_policy", code=400)
        if mcp_cfg and not isinstance(mcp_cfg, dict):
            return api_response(status="error", message="invalid_mcp_exposure_policy", code=400)
        if remote_hubs_cfg and not isinstance(remote_hubs_cfg, dict):
            return api_response(status="error", message="invalid_remote_hubs_exposure_policy", code=400)
        if isinstance(openai_cfg, dict) and "max_hops" in openai_cfg:
            try:
                if int(openai_cfg.get("max_hops")) < 1:
                    return api_response(status="error", message="invalid_openai_compat_max_hops", code=400)
            except (TypeError, ValueError):
                return api_response(status="error", message="invalid_openai_compat_max_hops", code=400)
        if isinstance(remote_hubs_cfg, dict) and "max_hops" in remote_hubs_cfg:
            try:
                if int(remote_hubs_cfg.get("max_hops")) < 1:
                    return api_response(status="error", message="invalid_remote_hubs_max_hops", code=400)
            except (TypeError, ValueError):
                return api_response(status="error", message="invalid_remote_hubs_max_hops", code=400)
        new_cfg["exposure_policy"] = get_exposure_policy_service().normalize_exposure_policy(exposure_cfg)
    if "terminal_policy" in new_cfg:
        terminal_cfg = new_cfg.get("terminal_policy")
        if not isinstance(terminal_cfg, dict):
            return api_response(status="error", message="invalid_terminal_policy", code=400)
        if "allowed_roles" in terminal_cfg and not isinstance(terminal_cfg.get("allowed_roles"), list):
            return api_response(status="error", message="invalid_terminal_allowed_roles", code=400)
        if "allowed_cidrs" in terminal_cfg and not isinstance(terminal_cfg.get("allowed_cidrs"), list):
            return api_response(status="error", message="invalid_terminal_allowed_cidrs", code=400)
        new_cfg["terminal_policy"] = get_platform_governance_service().normalize_terminal_policy(terminal_cfg)
    if "cli_session_mode" in new_cfg:
        mode_cfg = new_cfg.get("cli_session_mode")
        if not isinstance(mode_cfg, dict):
            return api_response(status="error", message="invalid_cli_session_mode", code=400)
        backends = [str(item or "").strip().lower() for item in list(mode_cfg.get("stateful_backends") or []) if str(item or "").strip()]
        for backend in backends:
            if backend not in {"sgpt", "codex", "opencode", "aider", "mistral_code", "deerflow", "ananta_research"}:
                return api_response(status="error", message="invalid_cli_session_backend", code=400)
        try:
            max_turns = int(mode_cfg.get("max_turns_per_session", 40))
            max_sessions = int(mode_cfg.get("max_sessions", 200))
        except (TypeError, ValueError):
            return api_response(status="error", message="invalid_cli_session_limits", code=400)
        if max_turns < 1 or max_turns > 200:
            return api_response(status="error", message="invalid_cli_session_max_turns", code=400)
        if max_sessions < 1 or max_sessions > 2000:
            return api_response(status="error", message="invalid_cli_session_max_sessions", code=400)
        reuse_scope = str(mode_cfg.get("reuse_scope") or "task").strip().lower()
        if reuse_scope not in {"task", "role"}:
            return api_response(status="error", message="invalid_cli_session_reuse_scope", code=400)
        new_cfg["cli_session_mode"] = {
            "enabled": bool(mode_cfg.get("enabled", False)),
            "stateful_backends": backends or ["opencode", "codex"],
            "max_turns_per_session": max_turns,
            "max_sessions": max_sessions,
            "allow_task_scoped_auto_session": bool(mode_cfg.get("allow_task_scoped_auto_session", True)),
            "reuse_scope": reuse_scope,
            "native_opencode_sessions": bool(mode_cfg.get("native_opencode_sessions", False)),
        }
    if "opencode_runtime" in new_cfg:
        opencode_runtime_cfg = new_cfg.get("opencode_runtime")
        if not isinstance(opencode_runtime_cfg, dict):
            return api_response(status="error", message="invalid_opencode_runtime", code=400)
        tool_mode = str(opencode_runtime_cfg.get("tool_mode") or "full").strip().lower()
        if tool_mode not in {"full", "readonly", "toolless"}:
            return api_response(status="error", message="invalid_opencode_tool_mode", code=400)
        execution_mode = str(opencode_runtime_cfg.get("execution_mode") or "live_terminal").strip().lower()
        if execution_mode not in {"backend", "live_terminal", "interactive_terminal"}:
            return api_response(status="error", message="invalid_opencode_execution_mode", code=400)
        interactive_launch_mode = str(opencode_runtime_cfg.get("interactive_launch_mode") or "run").strip().lower()
        if interactive_launch_mode not in {"run", "tui"}:
            return api_response(status="error", message="invalid_opencode_interactive_launch_mode", code=400)
        target_provider = str(opencode_runtime_cfg.get("target_provider") or "").strip().lower() or None
        if target_provider not in {None, "ollama", "lmstudio"}:
            return api_response(status="error", message="invalid_opencode_target_provider", code=400)
        new_cfg["opencode_runtime"] = {
            "tool_mode": tool_mode,
            "execution_mode": execution_mode,
            "interactive_launch_mode": interactive_launch_mode,
            "target_provider": target_provider,
        }
    if "worker_runtime" in new_cfg:
        worker_runtime_cfg = new_cfg.get("worker_runtime")
        if not isinstance(worker_runtime_cfg, dict):
            return api_response(status="error", message="invalid_worker_runtime", code=400)
        workspace_root = worker_runtime_cfg.get("workspace_root")
        workspace_root = str(workspace_root).strip() if workspace_root is not None else None
        workspace_reuse_mode = str(worker_runtime_cfg.get("workspace_reuse_mode") or "goal_worker").strip().lower() or "goal_worker"
        if workspace_reuse_mode not in {"task", "goal_worker"}:
            return api_response(status="error", message="invalid_worker_workspace_reuse_mode", code=400)
        new_cfg["worker_runtime"] = {
            "workspace_root": workspace_root or None,
            "workspace_reuse_mode": workspace_reuse_mode,
        }
    for key in ("role_model_overrides", "template_model_overrides", "task_kind_model_overrides"):
        if key not in new_cfg:
            continue
        override_cfg = new_cfg.get(key)
        if not isinstance(override_cfg, dict):
            return api_response(status="error", message=f"invalid_{key}", code=400)
        new_cfg[key] = shared.normalize_model_override_map(override_cfg)
    for key in ("llm_config", "research_backend", "opencode_runtime", "worker_runtime"):
        new_cfg = _merge_nested_config_block(current_cfg, new_cfg, key)
    if "hub_copilot" in new_cfg and isinstance(new_cfg["hub_copilot"], dict):
        merged_hub_copilot = (current_cfg.get("hub_copilot", {}) or {}).copy()
        merged_hub_copilot.update(new_cfg["hub_copilot"])
        new_cfg = {**new_cfg, "hub_copilot": shared.normalize_hub_copilot_config(merged_hub_copilot)}
    if "context_bundle_policy" in new_cfg and isinstance(new_cfg["context_bundle_policy"], dict):
        merged_context_bundle_policy = (current_cfg.get("context_bundle_policy", {}) or {}).copy()
        merged_context_bundle_policy.update(new_cfg["context_bundle_policy"])
        new_cfg = {
            **new_cfg,
            "context_bundle_policy": normalize_context_bundle_policy_config(merged_context_bundle_policy),
        }
    if "artifact_flow" in new_cfg and isinstance(new_cfg["artifact_flow"], dict):
        merged_artifact_flow = (current_cfg.get("artifact_flow", {}) or {}).copy()
        merged_artifact_flow.update(new_cfg["artifact_flow"])
        new_cfg = {**new_cfg, "artifact_flow": shared.normalize_artifact_flow_config(merged_artifact_flow)}

    current_cfg.update(new_cfg)
    current_app.config["AGENT_CONFIG"] = current_cfg
    sync_runtime_state(current_app, current_cfg, changed_keys=set(new_cfg.keys()))

    try:
        reserved_keys = {"data", "status", "message", "error", "code"}
        for key, value in new_cfg.items():
            if key not in reserved_keys:
                get_repository_registry().config_repo.save(ConfigDB(key=key, value_json=json.dumps(value)))
    except Exception as exc:
        current_app.logger.error(f"Fehler beim Speichern der Konfiguration in DB: {exc}")

    log_audit("config_updated", {"keys": list(new_cfg.keys())})
    return api_response(data={"status": "updated"})
