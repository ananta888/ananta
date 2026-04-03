from __future__ import annotations

import json

from flask import Blueprint, current_app, request

from agent.auth import admin_required, check_auth
from agent.common.api_envelope import unwrap_api_envelope
from agent.common.audit import log_audit
from agent.common.errors import api_response
from agent.config_defaults import sync_runtime_state
from agent.db_models import ConfigDB
from agent.services.context_bundle_service import normalize_context_bundle_policy_config
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
    return api_response(data=current_app.config.get("AGENT_CONFIG", {}))


@settings_bp.route("/config", methods=["POST"])
@admin_required
def set_config():
    new_cfg = request.get_json()
    if not isinstance(new_cfg, dict):
        return api_response(status="error", message="invalid_json", code=400)

    new_cfg = unwrap_config(new_cfg)
    current_cfg = current_app.config.get("AGENT_CONFIG", {})
    for key in ("llm_config", "research_backend"):
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
