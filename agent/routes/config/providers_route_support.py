"""Shared guards of the provider/model routes.

Feature-flag and capability checks and input/rate-limit error responses used by
:mod:`.providers` (catalog, default selection) and
:mod:`.providers_model_routing` (routing configuration API).
"""
from __future__ import annotations

from flask import current_app, g, request

from agent.common.audit import log_audit
from agent.common.errors import api_response
from agent.config import settings as runtime_settings
from agent.services.dashboard_feature_flag_service import (
    resolve_dashboard_feature_flags,
)
from agent.services.model_catalog_service import (
    ModelCatalogCapabilityPolicy,
)
from agent.services.surface_rate_limit_policy import (
    surface_rate_limit_policy,
)

MODEL_ROUTING_READ_CAPABILITY = "model_routing.read"
MODEL_ROUTING_VALIDATE_CAPABILITY = "model_routing.validate"
MODEL_ROUTING_EXPORT_CAPABILITY = "model_routing.export"
MODEL_ROUTING_MUTATE_CAPABILITY = "model_routing.mutate"


def _model_catalog_feature_enabled() -> bool:
    """The released catalog is canonical; retained flags no longer hide reads."""

    return True


def _model_catalog_v2_enabled() -> bool:
    """Catalog v2 passed its release gate and remains additive to v1."""

    return True


def _model_routing_editor_enabled() -> bool:
    app_cfg = current_app.config.get("AGENT_CONFIG", {}) or {}
    return resolve_dashboard_feature_flags(
        app_cfg,
        defaults={
            "feature_angular_model_dashboard_enabled": getattr(
                runtime_settings, "feature_angular_model_dashboard_enabled", False
            ),
            "feature_model_routing_editor_enabled": getattr(
                runtime_settings, "feature_model_routing_editor_enabled", False
            ),
        },
    ).model_routing_editor


def _routing_editor_disabled_response():
    return api_response(status="error", message="model_routing_editor_feature_disabled", code=404)


def _feature_disabled_response():
    return api_response(
        status="error",
        message="model_catalog_feature_disabled",
        code=404,
    )


def _capability_allowed(capability: str) -> bool:
    claims = {
        **dict(getattr(g, "auth_payload", {}) or {}),
        **dict(getattr(g, "user", {}) or {}),
    }
    return ModelCatalogCapabilityPolicy().allows(
        capability,
        is_admin=bool(getattr(g, "is_admin", False)),
        claims=claims,
    )


def _capability_denied_response(capability: str):
    log_audit(
        "model_catalog_capability_denied",
        {"capability": capability, "path": request.path},
    )
    return api_response(
        status="error",
        message="forbidden",
        data={"reason_code": "model_catalog_capability_required"},
        code=403,
    )


def _model_catalog_input_error(message: str):
    return api_response(
        status="error",
        message=message,
        code=400,
    )


def _query_args_are_valid(*allowed: str) -> bool:
    return not (set(request.args.keys()) - set(allowed))


def _refresh_body_is_valid() -> bool:
    body = request.get_json(silent=True)
    if body == {}:
        return True
    return body is None and not request.get_data(cache=True).strip()


def _surface_rate_limit_response(namespace: str):
    decision = surface_rate_limit_policy.consume(
        config=current_app.config,
        namespace=namespace,
        auth_payload=getattr(g, "auth_payload", None),
        user=getattr(g, "user", None),
        remote_addr=request.remote_addr,
    )
    if decision.allowed:
        return None
    result = api_response(
        status="error",
        message="rate_limit_exceeded",
        data={
            "reason_code": "rate_limit_exceeded",
            "retry_after_seconds": decision.retry_after_seconds,
        },
        code=429,
    )
    response = result[0] if isinstance(result, tuple) else result
    response.headers["Retry-After"] = str(decision.retry_after_seconds)
    return result

