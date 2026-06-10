"""CCARI-011: Hub Flask route for context_reload_request.

``POST /api/codecompass/reload-context`` — JSON body ``{task_id, request}``,
returns 200 with ``context_reload_response.v1`` payload, or 409 with the
same payload shape if the request is policy-blocked. The handler is
intentionally thin: all real work is in
``ContextDeliveryService.handle_reload_request``.
"""
from __future__ import annotations

import logging
from typing import Any

from flask import Blueprint, jsonify, request

from agent.services.context_delivery_service import get_context_delivery_service


log = logging.getLogger(__name__)

codecompass_reload_bp = Blueprint("codecompass_reload", __name__)


@codecompass_reload_bp.post("/api/codecompass/reload-context")
def reload_context() -> Any:
    """Validate a context_reload_request, look up the task, and serve chunks.

    - 200 OK with ``context_reload_response.v1`` payload on success.
    - 400 Bad Request if the body is malformed (missing task_id or request).
    - 404 Not Found if the task does not exist.
    - 409 Conflict if the request is policy-blocked (mutating, invalid
      query type, etc.). The body still carries the same response shape.
    """
    body = request.get_json(silent=True) or {}
    task_id = str(body.get("task_id") or "").strip()
    if not task_id:
        return jsonify({"status": "invalid_request", "code": "task_id_required"}), 400
    payload_request = body.get("request")
    if not isinstance(payload_request, dict):
        return jsonify({"status": "invalid_request", "code": "request_object_required"}), 400

    from agent.services.repository_registry import get_repository_registry

    repos = get_repository_registry()
    task = repos.task_repo.get_by_id(task_id)
    if task is None:
        return jsonify({"status": "task_not_found", "code": "task_not_found"}), 404

    svc = get_context_delivery_service()
    task_payload = task.model_dump() if hasattr(task, "model_dump") else dict(task)
    result = svc.handle_reload_request(task=task_payload, request=payload_request)
    if result.get("status") in ("policy_blocked", "invalid_request"):
        # The contract says policy_blocked returns HTTP 409. invalid_request
        # is also a request-shape problem; 409 is fine because the body has
        # the canonical shape.
        return jsonify(result), 409
    return jsonify(result), 200
