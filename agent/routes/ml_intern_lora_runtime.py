"""Admin-only Hub commands for isolated LoRA runtime management."""

from __future__ import annotations

from flask import Blueprint, current_app, g, request

from agent.auth import admin_required, check_auth
from agent.common.audit import log_audit
from agent.common.errors import api_response
from agent.services.ml_intern_adapter_registry_service import MlInternAdapterRegistryService
from agent.services.ml_intern_lora_inference_service import (
    get_lora_inference_service,
    resolve_lora_storage_config,
)
from agent.services.ml_intern_lora_runtime_management_service import (
    LoraRuntimeManagementError,
    MlInternLoraRuntimeManagementService,
)

ml_intern_lora_runtime_bp = Blueprint(
    "ml_intern_lora_runtime",
    __name__,
    url_prefix="/api/ml-intern-lora-runtime",
)


@ml_intern_lora_runtime_bp.get("/capabilities")
@check_auth
@admin_required
def capabilities():
    return api_response(data=dict(_service().capabilities()))


@ml_intern_lora_runtime_bp.post("/adapters/<adapter_id>/unload")
@check_auth
@admin_required
def unload(adapter_id: str):
    try:
        reason, _expected_version = _management_request()
        scope = _registry_scope()
        result = dict(_service().unload(adapter_id=adapter_id, reason=reason, **scope))
        log_audit(
            "ml_intern_lora_runtime_unload",
            {
                "adapter_id": adapter_id,
                "reason": reason,
                "reason_code": result.get("reason_code"),
            },
        )
        return api_response(data=result)
    except LoraRuntimeManagementError as exc:
        return _error(exc)


@ml_intern_lora_runtime_bp.post("/adapters/<adapter_id>/rollback")
@check_auth
@admin_required
def rollback(adapter_id: str):
    try:
        reason, expected_version = _management_request()
        scope = _registry_scope()
        result = _service().rollback(
            adapter_id=adapter_id,
            reason=reason,
            expected_version=expected_version,
            **scope,
        )
        log_audit(
            "ml_intern_lora_runtime_rollback",
            {
                "adapter_id": adapter_id,
                "reason": reason,
                "target_type": (result.get("rollback_target") or {}).get("type"),
                "unload_reason_code": (result.get("cache_unload") or {}).get("reason_code"),
            },
        )
        return api_response(data=result)
    except LoraRuntimeManagementError as exc:
        return _error(exc)


def _service() -> MlInternLoraRuntimeManagementService:
    agent_config = dict(current_app.config.get("AGENT_CONFIG", {}) or {})
    storage = resolve_lora_storage_config(agent_config)
    return MlInternLoraRuntimeManagementService(
        registry=MlInternAdapterRegistryService(storage["registry_path"]),
        inference=get_lora_inference_service(),
    )


def _management_request() -> tuple[str, int | None]:
    body = request.get_json(silent=True)
    if (
        not isinstance(body, dict)
        or not set(body).issubset({"confirmed", "reason", "expected_version"})
        or set(body) < {"confirmed", "reason"}
        or body.get("confirmed") is not True
    ):
        raise LoraRuntimeManagementError(
            "management_confirmation_required",
            "confirmed=true and a reason are required",
        )
    expected_version = body.get("expected_version")
    if "expected_version" in body and (
        isinstance(expected_version, bool)
        or not isinstance(expected_version, int)
        or not 1 <= expected_version <= 2_147_483_647
    ):
        raise LoraRuntimeManagementError(
            "adapter_expected_version_invalid",
            "expected_version must be a positive integer",
        )
    return str(body.get("reason") or "").strip(), expected_version


def _registry_scope() -> dict[str, str]:
    identity = dict(getattr(g, "user", {}) or getattr(g, "auth_payload", {}) or {})
    subject = str(
        identity.get("sub")
        or identity.get("username")
        or identity.get("agent_id")
        or "hub-admin"
    ).strip()
    tenant = str(identity.get("tenant_id") or identity.get("tenant") or subject).strip()
    return {"tenant_id": tenant, "owner_subject": subject}


def _error(exc: LoraRuntimeManagementError):
    if exc.retryable:
        code = 503
    elif exc.reason_code == "adapter_version_conflict":
        code = 409
    elif exc.reason_code == "adapter_not_found":
        code = 404
    else:
        code = 422
    return api_response(
        status="error",
        message=str(exc),
        data={"reason_code": exc.reason_code, "retryable": exc.retryable},
        code=code,
    )


__all__ = ["ml_intern_lora_runtime_bp"]
