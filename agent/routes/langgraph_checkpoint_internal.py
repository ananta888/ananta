"""POST-only internal API for the Hub-owned LangGraph checkpoint store."""

from __future__ import annotations

from flask import Blueprint, jsonify, request
from werkzeug.exceptions import RequestEntityTooLarge

from agent.auth import check_strict_auth
from agent.services.langgraph_checkpoint_gateway_runtime import (
    get_langgraph_checkpoint_gateway_service,
)
from agent.services.langgraph_checkpoint_gateway_service import (
    LangGraphCheckpointGatewayError,
)
from agent.services.workflow_hub_task_gateway_runtime import (
    WorkflowHubTaskConfigurationError,
)

langgraph_checkpoint_internal_bp = Blueprint(
    "langgraph_checkpoint_internal",
    __name__,
    url_prefix="/api/internal/workflow-runtime/langgraph",
)
_MAX_BODY_BYTES = 262_144


@langgraph_checkpoint_internal_bp.post("/checkpoints")
@check_strict_auth
def command_langgraph_checkpoint():
    try:
        if request.args:
            raise LangGraphCheckpointGatewayError("langgraph_checkpoint_query_transport_forbidden", status_code=400)
        body = _body()
        result = get_langgraph_checkpoint_gateway_service().execute(body)
        return jsonify({"data": result}), 200
    except LangGraphCheckpointGatewayError as exc:
        return _error(exc)
    except WorkflowHubTaskConfigurationError:
        return _error(LangGraphCheckpointGatewayError("langgraph_checkpoint_gateway_unavailable", status_code=503))


def _body() -> dict:
    content_length = request.content_length
    if content_length is not None and content_length > _MAX_BODY_BYTES:
        raise LangGraphCheckpointGatewayError("langgraph_checkpoint_payload_too_large", status_code=413)
    request.max_content_length = _MAX_BODY_BYTES
    try:
        raw = request.get_data(cache=True)
    except RequestEntityTooLarge as exc:
        raise LangGraphCheckpointGatewayError("langgraph_checkpoint_payload_too_large", status_code=413) from exc
    if len(raw) > _MAX_BODY_BYTES:
        raise LangGraphCheckpointGatewayError("langgraph_checkpoint_payload_too_large", status_code=413)
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise LangGraphCheckpointGatewayError("langgraph_checkpoint_json_required", status_code=400)
    return body


def _error(exc: LangGraphCheckpointGatewayError):
    return (
        jsonify(
            {
                "status": "error",
                "message": "langgraph checkpoint request failed",
                "data": {"reason_code": exc.reason_code},
            }
        ),
        exc.status_code,
    )


__all__ = ["langgraph_checkpoint_internal_bp"]
