"""Hub tool gateway for delegated workers, path A (WCRB-008).

POST /api/worker/v1/tasks/<task_id>/tools/<tool_name>
    body: {"arguments": {...}, "tool_call_id": "..."}  ->  the tool result

Registered worker only (scope ``codecompass.tools.execute``); admission, policy
and execution live in ``agent.services.worker_tool_gateway``.
"""

from __future__ import annotations

from flask import Blueprint, current_app, g, jsonify, request

from agent.auth import check_registered_worker_auth
from agent.common.errors import api_response
from agent.services.workflow_worker_service_auth import CODECOMPASS_TOOL_GATEWAY_SCOPE

worker_tool_gateway_bp = Blueprint("worker_tool_gateway", __name__)
MAX_BODY_BYTES = 64 * 1024


def _refused(reason: str, code: int):
    return api_response(status="error", message="forbidden", data={"reason_code": reason}, code=code)


@worker_tool_gateway_bp.route("/api/worker/v1/tasks/<task_id>/tools/<tool_name>", methods=["POST"])
@check_registered_worker_auth(scope=CODECOMPASS_TOOL_GATEWAY_SCOPE)
def call_worker_tool(task_id: str, tool_name: str):
    from agent.common.logging import get_correlation_id
    from agent.services.worker_tool_gateway import GatewayDenied, get_worker_tool_gateway

    if request.content_length is None or not 0 < request.content_length <= MAX_BODY_BYTES:
        return _refused("payload_invalid", 400)
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or not set(payload) <= {"arguments", "tool_call_id"}:
        return _refused("payload_invalid", 400)
    arguments = payload.get("arguments") or {}
    tool_call_id = str(payload.get("tool_call_id") or "")[:128]
    if not isinstance(arguments, dict):
        return _refused("arguments_not_object", 400)
    worker_url = str(dict(getattr(g, "service_identity", {}) or {}).get("worker_url") or "")
    try:
        result = get_worker_tool_gateway().call(
            current_app._get_current_object(), task_id, tool_name, arguments, worker_url=worker_url,
            tool_call_id=tool_call_id, trace_id=get_correlation_id())
    except GatewayDenied as denied:
        return _refused(denied.reason, denied.status_code)
    return jsonify(result)
