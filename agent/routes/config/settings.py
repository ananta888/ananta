from __future__ import annotations

import json

from flask import Blueprint, current_app, g, request

from agent.auth import admin_required, check_auth
from agent.common.api_envelope import unwrap_api_envelope
from agent.common.audit import log_audit
from agent.common.errors import api_response
from agent.config import settings as runtime_settings
from agent.config_defaults import sync_runtime_state
from agent.db_models import ConfigDB
from agent.governance_modes import resolve_governance_mode
from agent.routes.operation_gate import operation_gate
from agent.runtime_profiles import resolve_runtime_profile
from agent.services.dashboard_feature_flag_service import (
    resolve_dashboard_feature_flags,
)
from agent.services.governance_profile_service import build_effective_policy_profile
from agent.services.model_routing_legacy_migration_service import (
    build_model_routing_legacy_migration_service,
)
from agent.services.operation_policy_revision_service import (
    OperationPolicyRevisionError,
    get_operation_policy_revision_service,
)
from agent.services.operation_policy_service import (
    OperationPolicyConfigError,
    get_operation_policy_service,
)
from agent.services.repository_registry import get_repository_registry
from agent.services.template_variable_registry import (
    build_template_variable_registry_payload,
    resolve_allowed_template_variables,
)

from .config_update_pipeline import ConfigUpdateRejected, normalize_config_update

settings_bp = Blueprint("config_settings", __name__)


def _operation_policy_actor() -> str:
    user = getattr(g, "user", None)
    user = user if isinstance(user, dict) else {}
    return str(user.get("sub") or user.get("username") or "admin_via_token").strip() or "admin_via_token"


def _dashboard_feature_flags():
    defaults = {
        key: getattr(runtime_settings, key, False)
        for key in (
            "feature_angular_kanban_enabled",
            "feature_angular_model_dashboard_enabled",
            "feature_model_catalog_v2_enabled",
            "feature_model_routing_editor_enabled",
            "feature_legacy_model_picker_deprecation_enabled",
            "feature_tui_kanban_enabled",
            "feature_tui_model_menu_enabled",
        )
    }
    return resolve_dashboard_feature_flags(
        current_app.config.get("AGENT_CONFIG", {}),
        defaults=defaults,
    )


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


@settings_bp.route("/config", methods=["GET"])
@check_auth
@operation_gate("api.config.get")
def get_config():
    cfg = dict(current_app.config.get("AGENT_CONFIG", {}) or {})
    if bool(getattr(g, "is_admin", False)):
        cfg["operation_policy"] = get_operation_policy_service().public_projection(
            get_operation_policy_service().resolve_policy(cfg),
            include_history=False,
        )
    else:
        cfg.pop("operation_policy", None)
    cfg["template_variables_allowlist"] = resolve_allowed_template_variables(cfg)
    cfg["template_variable_registry"] = build_template_variable_registry_payload(agent_cfg=cfg)
    cfg["runtime_profile_effective"] = resolve_runtime_profile(cfg)
    cfg["governance_mode_effective"] = resolve_governance_mode(cfg)
    cfg["effective_policy_profile"] = build_effective_policy_profile(cfg)
    cfg["lora_adapter_registry"] = _build_lora_registry_summary(cfg)
    cfg["dashboard_feature_flags"] = _dashboard_feature_flags().as_dict()
    cfg["context_window_effective"] = _context_window_summary(cfg)
    return api_response(data=cfg)


def _context_window_summary(cfg: dict) -> dict:
    try:
        from agent.context_profile import describe_context_window

        return describe_context_window(cfg)
    except Exception as exc:  # noqa: BLE001 -- the settings read must not fail on a probe
        current_app.logger.warning("context window summary unavailable: %s", exc)
        return {"schema": "ananta.context_window.v1", "error": "unavailable"}


@settings_bp.route("/config/context-window", methods=["GET"])
@check_auth
def get_context_window():
    """Configured window, provider/model limits, effective window and the budgets derived from it."""
    return api_response(data=_context_window_summary(dict(current_app.config.get("AGENT_CONFIG", {}) or {})))


@settings_bp.route("/config/features/v1", methods=["GET"])
@check_auth
def get_dashboard_feature_flags():
    return api_response(data=_dashboard_feature_flags().as_dict())


def _build_lora_registry_summary(cfg: dict) -> dict:
    """Gibt lesbare Adapter-Registry-Zusammenfassung zurueck (keine sensiblen Pfade)."""
    try:
        from agent.services.ml_intern_adapter_registry_service import MlInternAdapterRegistryService
        from agent.services.ml_intern_training_config_service import normalize_lora_runtime_config

        lora_rt = normalize_lora_runtime_config(cfg.get("lora_runtime") or {})
        registry_path = lora_rt.get("adapter_registry_path", "artifacts/lora/adapter_registry.json")
        svc = MlInternAdapterRegistryService(registry_path)
        return svc.to_read_model()
    except Exception:
        return {"schema": "mlintern_adapter_registry.v1", "count": 0, "approved_count": 0, "items": []}


@settings_bp.route("/config", methods=["POST"])
@admin_required
@operation_gate("api.config.update.post")
def set_config():
    new_cfg = request.get_json()
    if not isinstance(new_cfg, dict):
        return api_response(status="error", message="invalid_json", code=400)

    new_cfg = unwrap_config(new_cfg)
    new_cfg.pop("dashboard_feature_flags", None)
    current_cfg = current_app.config.get("AGENT_CONFIG", {}) or {}
    try:
        new_cfg, policy_revision_update = normalize_config_update(
            new_cfg,
            current_cfg=current_cfg,
            actor=_operation_policy_actor,
            model_routing_migration_factory=build_model_routing_legacy_migration_service,
        )
    except ConfigUpdateRejected as rejection:
        return api_response(status="error", message=rejection.message, data=rejection.data, code=rejection.code)

    if policy_revision_update is not None and policy_revision_update.changed:
        serialized_policy = json.dumps(
            policy_revision_update.stored_policy,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        if not get_repository_registry().config_repo.compare_and_swap_json(
            key="operation_policy",
            expected_revision=policy_revision_update.previous_revision,
            value_json=serialized_policy,
        ):
            return api_response(status="error", message="operation_policy_revision_conflict", code=409)

    current_cfg.update(new_cfg)
    current_app.config["AGENT_CONFIG"] = current_cfg
    sync_runtime_state(current_app, current_cfg, changed_keys=set(new_cfg.keys()))

    try:
        reserved_keys = {"data", "status", "message", "error", "code"}
        for key, value in new_cfg.items():
            if key not in reserved_keys and not (key == "operation_policy" and policy_revision_update is not None):
                get_repository_registry().config_repo.save(ConfigDB(key=key, value_json=json.dumps(value)))
    except Exception as exc:
        current_app.logger.error(f"Fehler beim Speichern der Konfiguration in DB: {exc}")

    if policy_revision_update is not None and policy_revision_update.changed:
        log_audit(
            "operation_policy_updated",
            {
                "previous_revision": policy_revision_update.previous_revision,
                "revision": policy_revision_update.revision,
                "previous_policy_hash": policy_revision_update.previous_hash,
                "policy_hash": policy_revision_update.policy_hash,
                "actor": _operation_policy_actor(),
                "validated_diff": policy_revision_update.diff,
            },
        )
    log_audit("config_updated", {"keys": list(new_cfg.keys())})
    return api_response(data={"status": "updated"})


@settings_bp.route("/config/operation-policy/rollback", methods=["POST"])
@admin_required
@operation_gate("api.config.operation_policy.rollback.post")
def rollback_operation_policy():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return api_response(status="error", message="invalid_json", code=400)
    target_revision = payload.get("target_revision")
    expected_revision = payload.get("expected_revision")
    if isinstance(target_revision, bool) or not isinstance(target_revision, int):
        return api_response(status="error", message="operation_policy_target_revision_invalid", code=400)
    if isinstance(expected_revision, bool) or not isinstance(expected_revision, int):
        return api_response(status="error", message="operation_policy_expected_revision_invalid", code=400)
    current_cfg = current_app.config.get("AGENT_CONFIG", {}) or {}
    current_stored = current_cfg.get("operation_policy")
    try:
        update = get_operation_policy_revision_service().prepare_rollback(
            current_stored=current_stored if isinstance(current_stored, dict) else None,
            target_revision=target_revision,
            expected_revision=expected_revision,
            actor=_operation_policy_actor(),
        )
    except OperationPolicyConfigError as exc:
        return api_response(status="error", message=exc.reason_code, code=400)
    except OperationPolicyRevisionError as exc:
        return api_response(
            status="error",
            message=exc.reason_code,
            code=409 if exc.conflict else 400,
        )
    serialized = json.dumps(update.stored_policy, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    if not get_repository_registry().config_repo.compare_and_swap_json(
        key="operation_policy",
        expected_revision=update.previous_revision,
        value_json=serialized,
    ):
        return api_response(status="error", message="operation_policy_revision_conflict", code=409)
    current_cfg["operation_policy"] = update.stored_policy
    current_app.config["AGENT_CONFIG"] = current_cfg
    sync_runtime_state(current_app, current_cfg, changed_keys={"operation_policy"})
    log_audit(
        "operation_policy_rolled_back",
        {
            "target_revision": target_revision,
            "previous_revision": update.previous_revision,
            "revision": update.revision,
            "previous_policy_hash": update.previous_hash,
            "policy_hash": update.policy_hash,
            "actor": _operation_policy_actor(),
            "validated_diff": update.diff,
        },
    )
    return api_response(
        data=get_operation_policy_service().public_projection(
            get_operation_policy_service().resolve_policy(current_cfg),
            include_history=True,
        )
    )
