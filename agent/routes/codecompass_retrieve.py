"""HTTP adapter for the shared CodeCompass agentic retrieval contract.

POST /api/codecompass/retrieve is the n8n/HTTP entry. It uses the same
service as the worker tool loop and MCP. Clients cannot supply backend
credentials, collection names or a server capability.
"""

from __future__ import annotations

from flask import Blueprint, current_app, g, request

from agent.auth import check_auth
from agent.common.errors import BadRequestError, api_response
from agent.routes.source_control_access import authorize_route_request
from agent.services.source_control_access_policy import SourceControlAction

codecompass_retrieve_bp = Blueprint("codecompass_retrieve", __name__)

_FORBIDDEN_REQUEST_KEYS = frozenset(
    {
        "collection",
        "collection_name",
        "qdrant",
        "api_key",
        "authorization",
        "capability",
        "credentials",
    }
)


@codecompass_retrieve_bp.before_request
@check_auth
def _authorize_codecompass_retrieve_surface():
    return authorize_route_request(
        action=SourceControlAction.query,
        resource_kind="task_context",
        collection=True,
        require_project_scope=False,
    )


def _contains_authority_field(value) -> bool:
    from agent.services.codecompass_authority_policy import contains_client_authority

    return contains_client_authority(value)


def _server_capability(requested_scope: dict) -> dict | None:
    from agent.services.codecompass_retrieval_capability_service import (
        resolve_request_capability,
    )

    return resolve_request_capability(
        application=current_app,
        principal=getattr(g, "source_control_principal", None),
        requested_scope=requested_scope,
    )


@codecompass_retrieve_bp.route("/api/codecompass/retrieve", methods=["POST"])
def retrieve_codecompass_context():
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        raise BadRequestError("invalid_json_object")
    if _contains_authority_field(body):
        raise BadRequestError("backend_fields_not_allowed")
    query = str(body.get("query") or "").strip()
    if not query:
        raise BadRequestError("query_required")

    from agent.services.codecompass_agentic_retrieval_contract import (
        KIND_REQUEST,
        SCHEMA_ID,
        validate_request,
    )
    from agent.services.codecompass_agentic_retrieval_service import (
        get_codecompass_agentic_retrieval_service,
    )

    payload = dict(body)
    payload.setdefault("schema", SCHEMA_ID)
    payload.setdefault("kind", KIND_REQUEST)
    try:
        request_payload = validate_request(payload)
    except Exception as exc:
        raise BadRequestError(str(getattr(exc, "reason", "") or exc)) from exc
    result = get_codecompass_agentic_retrieval_service().retrieve(
        request_payload,
        capability=_server_capability(request_payload.get("scope") or {}),
    )
    status = "success" if result.get("status") in {"ok", "degraded", "empty"} else "error"
    code = 200 if status == "success" else 400
    return api_response(result, status=status, code=code)


@codecompass_retrieve_bp.route("/api/codecompass/sira/status", methods=["GET"])
def codecompass_sira_status():
    """Return a redacted Hub-owned read model; never inspect Worker files here."""

    from agent.config import settings
    from agent.services.codecompass_sira_status_service import CodeCompassSiraStatusService

    result = CodeCompassSiraStatusService().build(
        settings=settings,
        model_catalog=current_app.extensions.get("codecompass_sira_model_catalog"),
        worker_status=current_app.extensions.get("codecompass_sira_worker_status"),
    )
    return api_response(result)


@codecompass_retrieve_bp.route("/api/codecompass/sira/operations", methods=["POST"])
def create_codecompass_sira_operation():
    """Queue one fully automated operation; no approval workflow is involved."""

    authorization_error = authorize_route_request(
        action=SourceControlAction.index,
        resource_kind="sira_index_operation",
        collection=True,
        require_project_scope=True,
    )
    if authorization_error is not None:
        return authorization_error
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise BadRequestError("invalid_json_object")
    if _contains_authority_field(body):
        raise BadRequestError("backend_fields_not_allowed")
    allowed = {
        "operation",
        "repository_id",
        "snapshot_artifact_id",
        "idempotency_key",
    }
    unknown = sorted(set(body).difference(allowed))
    if unknown:
        raise BadRequestError("sira_index_operation_unknown_fields:" + ",".join(unknown))
    principal = g.source_control_principal
    try:
        from agent.services.codecompass_sira_index_operation_service import (
            SiraIndexOperationConflict,
            get_codecompass_sira_index_operation_service,
        )

        result = get_codecompass_sira_index_operation_service().submit(
            operation=str(body.get("operation") or ""),
            tenant_id=str(principal.tenant_id or ""),
            project_id=str(principal.project_id or ""),
            repository_id=str(body.get("repository_id") or ""),
            snapshot_artifact_id=str(body.get("snapshot_artifact_id") or ""),
            idempotency_key=str(body.get("idempotency_key") or ""),
            actor_id=str(principal.subject_id or ""),
        )
    except SiraIndexOperationConflict as exc:
        return api_response(
            status="error",
            message=str(exc),
            data={"reason_code": str(exc)},
            code=409,
        )
    except ValueError as exc:
        raise BadRequestError(str(exc)) from exc
    return api_response(result, code=202)


@codecompass_retrieve_bp.route(
    "/api/codecompass/sira/operations/<operation_id>",
    methods=["GET"],
)
def get_codecompass_sira_operation(operation_id: str):
    principal = g.source_control_principal
    if not principal.tenant_id or not principal.project_id:
        return api_response(
            status="error",
            message="forbidden",
            data={"reason_code": "source_control_principal_scope_required"},
            code=403,
        )
    from agent.services.codecompass_sira_index_operation_service import (
        get_codecompass_sira_index_operation_service,
    )

    result = get_codecompass_sira_index_operation_service().get(
        operation_id,
        tenant_id=principal.tenant_id,
        project_id=principal.project_id,
    )
    if result is None:
        return api_response(status="error", message="not_found", code=404)
    return api_response(result)
