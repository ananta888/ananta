"""Read-only projection and Hub-task refresh admission for local runtimes."""

from __future__ import annotations

from typing import Any, Protocol

from flask import Blueprint, current_app, g, request

from agent.auth import check_auth
from agent.common.audit import log_audit
from agent.common.errors import api_response
from agent.services.local_runtime_capability_composition import (
    local_runtime_capability_projection,
)
from agent.services.model_catalog_service import (
    MODEL_CATALOG_REFRESH_CAPABILITY,
    ModelCatalogCapabilityPolicy,
)
from agent.services.surface_rate_limit_policy import (
    MODEL_CATALOG_REFRESH,
    surface_rate_limit_policy,
)

local_runtime_capabilities_bp = Blueprint(
    "local_runtime_capabilities", __name__, url_prefix="/api/models/runtime-capabilities/v1"
)


class RuntimeCapabilityProjectionPort(Protocol):
    def snapshot(self) -> dict[str, Any]: ...


class RuntimeCapabilityRefreshDispatchPort(Protocol):
    def dispatch(self, *, provider_id: str | None, requested_by: str) -> str: ...


def _claims() -> dict[str, Any]:
    return {
        **dict(getattr(g, "auth_payload", {}) or {}),
        **dict(getattr(g, "user", {}) or {}),
    }


def _can_refresh() -> bool:
    return ModelCatalogCapabilityPolicy().allows(
        MODEL_CATALOG_REFRESH_CAPABILITY,
        is_admin=bool(getattr(g, "is_admin", False)),
        claims=_claims(),
    )


@local_runtime_capabilities_bp.get("")
@check_auth
def get_runtime_capabilities():
    projection = (
        current_app.extensions.get("local_runtime_capability_projection")
        or local_runtime_capability_projection()
    )
    return api_response(data=projection.snapshot())


@local_runtime_capabilities_bp.post("/refresh")
@check_auth
def request_runtime_capability_refresh():
    if not _can_refresh():
        return api_response(status="error", message="forbidden", code=403)
    body = request.get_json(silent=True)
    if body is None:
        body = {}
    if not isinstance(body, dict) or set(body).difference({"provider_id"}):
        return api_response(status="error", message="local_runtime_refresh_request_invalid", code=400)
    provider = body.get("provider_id")
    if provider is not None and provider not in {"ollama", "lmstudio"}:
        return api_response(status="error", message="local_runtime_provider_unknown", code=400)
    rate_limit = surface_rate_limit_policy.consume(
        config=current_app.config,
        namespace=MODEL_CATALOG_REFRESH,
        auth_payload=getattr(g, "auth_payload", None),
        user=getattr(g, "user", None),
        remote_addr=request.remote_addr,
    )
    if not rate_limit.allowed:
        return api_response(
            status="error",
            message="rate_limit_exceeded",
            data={
                "reason_code": "rate_limit_exceeded",
                "retry_after_seconds": rate_limit.retry_after_seconds,
            },
            code=429,
        )
    dispatcher = current_app.extensions.get("local_runtime_capability_refresh_dispatch")
    if dispatcher is None:
        return api_response(status="error", message="local_runtime_refresh_dispatch_unavailable", code=503)
    try:
        task_ref = dispatcher.dispatch(
            provider_id=provider,
            requested_by=str(_claims().get("sub") or _claims().get("id") or "authenticated-principal")[:192],
        )
    except ValueError as exc:
        reason = str(exc)
        if reason not in {"local_runtime_provider_unknown", "local_runtime_provider_unconfigured"}:
            reason = "local_runtime_refresh_dispatch_failed"
        return api_response(status="error", message=reason, code=503)
    log_audit("local_runtime_capability_refresh_requested", {"provider_id": provider, "task_ref": task_ref})
    return api_response(data={"schema": "ananta.local-runtime-refresh-reference.v1", "task_ref": task_ref}, code=202)


__all__ = ["local_runtime_capabilities_bp"]
