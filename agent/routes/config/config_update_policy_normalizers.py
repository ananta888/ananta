"""Governance and policy normalization steps of the POST /config update.

Every step keeps the validation rules and error messages of the former
monolithic ``set_config`` handler; see :mod:`config_update_contract`.
"""

from __future__ import annotations

import os

from flask import current_app

from agent.governance_modes import governance_mode_catalog
from agent.routes.config.config_update_contract import (
    ConfigUpdateContext,
    ConfigUpdateRejected,
    require_dict,
)
from agent.runtime_profiles import runtime_profile_catalog
from agent.services.dashboard_feature_flag_service import (
    DashboardFeatureFlagError,
    normalize_feature_flag_update,
)
from agent.services.exposure_policy_service import get_exposure_policy_service
from agent.services.operation_policy_revision_service import (
    OperationPolicyRevisionError,
    get_operation_policy_revision_service,
)
from agent.services.operation_policy_service import OperationPolicyConfigError
from agent.services.platform_governance_service import get_platform_governance_service
from agent.services.remote_federation_policy_service import get_remote_federation_policy_service
from agent.services.result_memory_service import normalize_result_memory_policy
from agent.services.routing_decision_service import get_routing_decision_service


def normalize_feature_flags(new_cfg: dict, ctx: ConfigUpdateContext) -> dict:
    try:
        new_cfg.update(normalize_feature_flag_update(new_cfg))
    except DashboardFeatureFlagError as exc:
        raise ConfigUpdateRejected(exc.reason_code) from exc
    return new_cfg


def check_model_routing_editor_release_gate(new_cfg: dict, ctx: ConfigUpdateContext) -> dict:
    current_cfg = ctx.current_cfg
    if (
        new_cfg.get("feature_model_routing_editor_enabled") is not True
        or current_cfg.get("feature_model_routing_editor_enabled") is True
    ):
        return new_cfg
    migration = ctx.model_routing_migration_factory(
        legacy_config=dict(current_cfg or {}),
        model_profiles_path=str(
            current_app.config.get("MODEL_PROFILES_PATH") or os.environ.get("MODEL_PROFILES_PATH") or ""
        ).strip(),
    )
    release_gate = migration.release_gate()
    if not release_gate.ready:
        raise ConfigUpdateRejected(
            "model_routing_editor_release_gate_failed",
            code=409,
            data=release_gate.model_dump(mode="json", by_alias=True),
        )
    return new_cfg


def normalize_context_window(new_cfg: dict, ctx: ConfigUpdateContext) -> dict:
    if "context_window" in new_cfg:
        from agent.context_profile import normalize_context_window_config

        try:
            new_cfg["context_window"] = normalize_context_window_config(new_cfg.get("context_window"))
        except ValueError as exc:
            raise ConfigUpdateRejected(str(exc)) from exc
    return new_cfg


def prepare_operation_policy_revision(new_cfg: dict, ctx: ConfigUpdateContext) -> dict:
    if "operation_policy" not in new_cfg:
        return new_cfg
    requested_policy = require_dict(new_cfg.get("operation_policy"), "operation_policy_object_required")
    current_stored = ctx.current_cfg.get("operation_policy")
    try:
        update = get_operation_policy_revision_service().prepare_update(
            current_stored=current_stored if isinstance(current_stored, dict) else None,
            requested=requested_policy,
            actor=ctx.actor(),
        )
    except OperationPolicyConfigError as exc:
        raise ConfigUpdateRejected(exc.reason_code, data={"field": exc.field} if exc.field else {}) from exc
    except OperationPolicyRevisionError as exc:
        raise ConfigUpdateRejected(exc.reason_code, code=409 if exc.conflict else 400) from exc
    ctx.policy_revision_update = update
    new_cfg["operation_policy"] = update.stored_policy
    return new_cfg


def normalize_profile_and_mode_selectors(new_cfg: dict, ctx: ConfigUpdateContext) -> dict:
    if "runtime_profile" in new_cfg:
        requested_profile = str(new_cfg.get("runtime_profile") or "").strip().lower()
        if requested_profile not in runtime_profile_catalog():
            raise ConfigUpdateRejected("invalid_runtime_profile")
        new_cfg["runtime_profile"] = requested_profile
    if "platform_mode" in new_cfg:
        requested_mode = str(new_cfg.get("platform_mode") or "").strip().lower()
        governance_service = get_platform_governance_service()
        if not governance_service.is_supported_platform_mode(requested_mode):
            raise ConfigUpdateRejected("invalid_platform_mode")
        new_cfg["platform_mode"] = governance_service.normalize_platform_mode(requested_mode)
    if "auth_provider" in new_cfg:
        requested_auth_provider = str(new_cfg.get("auth_provider") or "").strip().lower() or "local"
        if requested_auth_provider not in {"local", "oidc_bff"}:
            raise ConfigUpdateRejected("invalid_auth_provider")
        new_cfg["auth_provider"] = requested_auth_provider
    if "governance_mode" in new_cfg:
        requested = str(new_cfg.get("governance_mode") or "").strip().lower()
        if requested and requested not in governance_mode_catalog():
            raise ConfigUpdateRejected("invalid_governance_mode")
        new_cfg["governance_mode"] = requested or "balanced"
    for flag in ("goal_scoped_config_enabled", "goal_scoped_config_enforce_snapshot"):
        if flag in new_cfg:
            new_cfg[flag] = bool(new_cfg.get(flag))
    return new_cfg


def normalize_execution_fallback_policy(new_cfg: dict, ctx: ConfigUpdateContext) -> dict:
    if "execution_fallback_policy" not in new_cfg:
        return new_cfg
    fallback_cfg = require_dict(new_cfg.get("execution_fallback_policy"), "invalid_execution_fallback_policy")
    normalized_fallback = {
        "allow_hub_worker_fallback": bool(fallback_cfg.get("allow_hub_worker_fallback", True)),
        "escalate_on_fallback_block": bool(fallback_cfg.get("escalate_on_fallback_block", True)),
        "fallback_block_status": str(fallback_cfg.get("fallback_block_status") or "blocked").strip().lower()
        or "blocked",
    }
    if normalized_fallback["fallback_block_status"] not in {"blocked", "failed", "todo"}:
        raise ConfigUpdateRejected("invalid_fallback_block_status")
    new_cfg["execution_fallback_policy"] = normalized_fallback
    return new_cfg


def normalize_routing_memory_and_federation_policies(new_cfg: dict, ctx: ConfigUpdateContext) -> dict:
    if "routing_fallback_policy" in new_cfg:
        routing_fallback_cfg = require_dict(new_cfg.get("routing_fallback_policy"), "invalid_routing_fallback_policy")
        if "fallback_order" in routing_fallback_cfg and not isinstance(
            routing_fallback_cfg.get("fallback_order"), list
        ):
            raise ConfigUpdateRejected("invalid_routing_fallback_order")
        new_cfg["routing_fallback_policy"] = get_routing_decision_service().normalize_fallback_policy(
            routing_fallback_cfg
        )
    if "result_memory_policy" in new_cfg:
        memory_cfg = require_dict(new_cfg.get("result_memory_policy"), "invalid_result_memory_policy")
        new_cfg["result_memory_policy"] = normalize_result_memory_policy(memory_cfg)
    if "remote_federation_policy" in new_cfg:
        federation_cfg = require_dict(new_cfg.get("remote_federation_policy"), "invalid_remote_federation_policy")
        if "allowed_operations" in federation_cfg and not isinstance(federation_cfg.get("allowed_operations"), list):
            raise ConfigUpdateRejected("invalid_remote_federation_operations")
        new_cfg["remote_federation_policy"] = get_remote_federation_policy_service().normalize_policy(federation_cfg)
    if "autonomous_resilience" in new_cfg:
        resilience_cfg = require_dict(new_cfg.get("autonomous_resilience"), "invalid_autonomous_resilience")
        strategy = str(resilience_cfg.get("retry_backoff_strategy") or "exponential").strip().lower()
        if strategy not in {"constant", "exponential"}:
            raise ConfigUpdateRejected("invalid_retry_backoff_strategy")
    return new_cfg


def _require_positive_max_hops(section_cfg, message: str) -> None:
    if not (isinstance(section_cfg, dict) and "max_hops" in section_cfg):
        return
    try:
        if int(section_cfg.get("max_hops")) < 1:
            raise ConfigUpdateRejected(message)
    except (TypeError, ValueError) as exc:
        raise ConfigUpdateRejected(message) from exc


def normalize_exposure_policy(new_cfg: dict, ctx: ConfigUpdateContext) -> dict:
    if "exposure_policy" not in new_cfg:
        return new_cfg
    exposure_cfg = require_dict(new_cfg.get("exposure_policy"), "invalid_exposure_policy")
    openai_cfg = exposure_cfg.get("openai_compat", {})
    mcp_cfg = exposure_cfg.get("mcp", {})
    remote_hubs_cfg = exposure_cfg.get("remote_hubs", {})
    if openai_cfg and not isinstance(openai_cfg, dict):
        raise ConfigUpdateRejected("invalid_openai_compat_exposure_policy")
    if mcp_cfg and not isinstance(mcp_cfg, dict):
        raise ConfigUpdateRejected("invalid_mcp_exposure_policy")
    if remote_hubs_cfg and not isinstance(remote_hubs_cfg, dict):
        raise ConfigUpdateRejected("invalid_remote_hubs_exposure_policy")
    _require_positive_max_hops(openai_cfg, "invalid_openai_compat_max_hops")
    _require_positive_max_hops(remote_hubs_cfg, "invalid_remote_hubs_max_hops")
    new_cfg["exposure_policy"] = get_exposure_policy_service().normalize_exposure_policy(exposure_cfg)
    return new_cfg


def normalize_terminal_policy(new_cfg: dict, ctx: ConfigUpdateContext) -> dict:
    if "terminal_policy" not in new_cfg:
        return new_cfg
    terminal_cfg = require_dict(new_cfg.get("terminal_policy"), "invalid_terminal_policy")
    if "allowed_roles" in terminal_cfg and not isinstance(terminal_cfg.get("allowed_roles"), list):
        raise ConfigUpdateRejected("invalid_terminal_allowed_roles")
    if "allowed_cidrs" in terminal_cfg and not isinstance(terminal_cfg.get("allowed_cidrs"), list):
        raise ConfigUpdateRejected("invalid_terminal_allowed_cidrs")
    new_cfg["terminal_policy"] = get_platform_governance_service().normalize_terminal_policy(terminal_cfg)
    return new_cfg


def normalize_context_strategy(new_cfg: dict, ctx: ConfigUpdateContext) -> dict:
    if "context_strategy" not in new_cfg:
        return new_cfg
    # LCTX-004: validated and clamped; an unknown mode is an error, not a silent default
    raw_strategy = require_dict(new_cfg.get("context_strategy"), "invalid_context_strategy")
    from agent.services.context_strategy_service import MODES, normalize_config

    if "mode" in raw_strategy and str(raw_strategy.get("mode") or "").strip().lower() not in MODES:
        raise ConfigUpdateRejected("invalid_context_strategy_mode")
    return {
        **new_cfg,
        "context_strategy": normalize_config({**(ctx.current_cfg.get("context_strategy") or {}), **raw_strategy}),
    }


def normalize_decision_providers(new_cfg: dict, ctx: ConfigUpdateContext) -> dict:
    if "decision_providers" not in new_cfg:
        return new_cfg
    # DPRV: validated as a whole; TypeSafe Jev needs external_calls_allowed, keys only via api_key_env
    from agent.services.decision_providers.config import DecisionConfigError, normalize_decision_config

    try:
        decision_cfg = normalize_decision_config(
            new_cfg.get("decision_providers"), ctx.current_cfg.get("decision_providers") or {}
        )
    except DecisionConfigError as error:
        raise ConfigUpdateRejected(error.reason_code) from error
    return {**new_cfg, "decision_providers": decision_cfg}


_EMBEDDING_PROVIDERS = {"local_hash", "openai", "openai_compatible", "ollama", "mock"}
_EMBEDDING_OPTIONAL_TEXT_FIELDS = ("base_url", "policy_profile", "api_key_ref", "api_key_env", "model")


def normalize_embedding_provider(new_cfg: dict, ctx: ConfigUpdateContext) -> dict:
    if "embedding_provider" not in new_cfg:
        return new_cfg
    emb_cfg = require_dict(new_cfg.get("embedding_provider"), "invalid_embedding_provider")
    provider_val = str(emb_cfg.get("provider") or "local_hash").strip().lower()
    if provider_val not in _EMBEDDING_PROVIDERS:
        raise ConfigUpdateRejected(f"invalid_embedding_provider_value:{provider_val}")
    # Security: external providers require explicit opt-in
    is_external = provider_val in {"openai", "openai_compatible"}
    if is_external and not bool(emb_cfg.get("external_calls_allowed", False)):
        raise ConfigUpdateRejected("embedding_provider_external_calls_not_allowed")
    # api_key must not be stored in config — must come from env
    if "api_key" in emb_cfg:
        raise ConfigUpdateRejected("embedding_provider_api_key_not_allowed_in_config_use_env_var")
    allowed_base_urls = emb_cfg.get("allowed_base_urls")
    if allowed_base_urls is not None and not isinstance(allowed_base_urls, list):
        raise ConfigUpdateRejected("invalid_embedding_provider_allowed_base_urls")
    normalized_emb: dict = {
        "provider": provider_val,
        "external_calls_allowed": is_external and bool(emb_cfg.get("external_calls_allowed", False)),
    }
    if allowed_base_urls is not None:
        normalized_emb["allowed_base_urls"] = [str(u).strip() for u in allowed_base_urls if str(u).strip()]
    for field_name in _EMBEDDING_OPTIONAL_TEXT_FIELDS:
        if emb_cfg.get(field_name):
            normalized_emb[field_name] = str(emb_cfg[field_name]).strip()
    merged_emb = {**(ctx.current_cfg.get("embedding_provider") or {}), **normalized_emb}
    return {**new_cfg, "embedding_provider": merged_emb}
