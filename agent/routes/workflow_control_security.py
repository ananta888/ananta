"""HTTP security boundary shared by workflow control routes."""

from __future__ import annotations

from typing import Any

from flask import g, jsonify, request
from werkzeug.exceptions import RequestEntityTooLarge

from agent.auth import get_request_auth_context
from agent.common.audit import log_audit
from agent.common.errors import api_response
from agent.common.redaction import VisibilityLevel, redact
from agent.services.workflow_backend_factory import WorkflowBackendConfigurationError
from agent.services.workflow_control_composition import get_workflow_backend_control_facade
from agent.services.workflow_route_authorization_service import (
    WorkflowRoutePrincipal,
    workflow_route_authorization_service,
)

MAX_WORKFLOW_REQUEST_BYTES = 512 * 1024
MAX_WORKFLOW_SIGNAL_BYTES = 16 * 1024
MAX_WORKFLOW_CANCEL_BYTES = 8 * 1024
MAX_WORKFLOW_ID_LENGTH = 160


def workflow_json_body(*, max_bytes: int, required: bool = True) -> tuple[dict[str, Any] | None, object | None]:
    content_length = request.content_length
    if content_length is not None and content_length > max_bytes:
        return None, _payload_too_large(max_bytes)
    request.max_content_length = max_bytes
    try:
        raw = request.get_data(cache=True)
    except RequestEntityTooLarge:
        return None, _payload_too_large(max_bytes)
    if len(raw) > max_bytes:
        return None, _payload_too_large(max_bytes)
    if not raw and not required:
        return {}, None
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return None, api_response(
            status="error",
            message="JSON object body required",
            data={"reason_code": "workflow_invalid_json"},
            code=400,
        )
    return body, None


def workflow_principal() -> WorkflowRoutePrincipal:
    context = dict(get_request_auth_context() or {})
    if not context and bool(getattr(g, "is_admin", False)):
        context = {"sub": "hub", "tenant_id": "system"}
    return WorkflowRoutePrincipal.from_auth_context(context)


def validate_workflow_id(workflow_id: str):
    normalized = str(workflow_id or "").strip()
    if not normalized or len(normalized) > MAX_WORKFLOW_ID_LENGTH:
        return api_response(
            status="error",
            message="invalid workflow id",
            data={"reason_code": "workflow_id_invalid"},
            code=400,
        )
    return None


def require_workflow_owner(workflow_id: str):
    invalid = validate_workflow_id(workflow_id)
    if invalid is not None:
        return None, invalid
    try:
        principal = workflow_principal()
    except ValueError:
        return None, api_response(
            status="error",
            message="authenticated workflow principal required",
            data={"reason_code": "workflow_principal_required"},
            code=401,
        )
    authorized = workflow_route_authorization_service.is_authorized(workflow_id, principal)
    if not authorized:
        try:
            # Composition installs the persistent owner resolver.  This call
            # lets the route cache recover an existing owner after restart.
            get_workflow_backend_control_facade()
        except Exception as exc:  # noqa: BLE001
            log_audit(
                "workflow_control_owner_recovery_failed",
                {"workflow_id": workflow_id, "exception_type": type(exc).__name__},
            )
        authorized = workflow_route_authorization_service.is_authorized(workflow_id, principal)
    if not authorized:
        return None, api_response(
            status="error",
            message="workflow not found",
            data={"reason_code": "workflow_run_not_found"},
            code=404,
        )
    return principal, None


def backend_error(reason_code: str, *, code: int = 503):
    return api_response(
        status="error",
        message="workflow backend unavailable" if code >= 500 else "workflow request rejected",
        data={"reason_code": reason_code, "retryable": code >= 500},
        code=code,
    )


def backend_result(payload: dict[str, Any], *, success_code: int = 200):
    if not isinstance(payload, dict):
        return backend_error("workflow_backend_invalid_response", code=502)
    status = str(payload.get("status") or "").strip().lower()
    if status in {"degraded", "unavailable"}:
        return backend_error("workflow_backend_unavailable", code=503)
    if status == "not_found":
        return backend_error("workflow_run_not_found", code=404)
    return jsonify(_safe_backend_payload(payload)), success_code


def configured_workflow_backend(principal: WorkflowRoutePrincipal):
    """Return an authorized view of the single Hub workflow-control facade."""

    try:
        return get_workflow_backend_control_facade().bind(principal), None
    except WorkflowBackendConfigurationError as exc:
        log_audit(
            "workflow_backend_configuration_rejected",
            {"reason_code": exc.reason_code, "backend": exc.backend},
        )
        return None, backend_error("workflow_backend_invalid", code=503)
    except Exception as exc:  # noqa: BLE001
        log_audit(
            "workflow_backend_initialization_failed",
            {"exception_type": type(exc).__name__},
        )
        return None, backend_error("workflow_backend_unavailable", code=503)


def _payload_too_large(max_bytes: int):
    return api_response(
        status="error",
        message="workflow payload too large",
        data={"reason_code": "workflow_payload_too_large", "max_bytes": max_bytes},
        code=413,
    )


def _safe_backend_payload(payload: dict[str, Any]) -> dict[str, Any]:
    safe = dict(redact(payload, VisibilityLevel.USER) or {})
    temporal = payload.get("temporal")
    if isinstance(temporal, dict):
        run_id = str(temporal.get("run_id") or "").strip()
        safe["temporal"] = {"run_id": run_id} if run_id else {}
    return safe
