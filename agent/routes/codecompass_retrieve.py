"""HTTP adapter for the shared CodeCompass agentic retrieval contract.

POST /api/codecompass/retrieve is the n8n/HTTP entry. It uses the same
service as the worker tool loop and MCP. Clients cannot supply backend
credentials, collection names or a server capability.
"""

from __future__ import annotations

from flask import Blueprint, current_app, request

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
    )


def _server_capability() -> dict | None:
    cfg = current_app.config.get("AGENT_CONFIG", {}) or {}
    raw = (cfg.get("codecompass_retrieval") or {}).get("capability")
    return dict(raw) if isinstance(raw, dict) else None


@codecompass_retrieve_bp.route("/api/codecompass/retrieve", methods=["POST"])
def retrieve_codecompass_context():
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        raise BadRequestError("invalid_json_object")
    forbidden = sorted(key for key in body if str(key).lower() in _FORBIDDEN_REQUEST_KEYS)
    if forbidden:
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
        capability=_server_capability(),
    )
    status = "success" if result.get("status") in {"ok", "degraded", "empty"} else "error"
    code = 200 if status == "success" else 400
    return api_response(result, status=status, code=code)
