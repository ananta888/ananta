"""Authenticated, runtime-neutral Hub capability projection."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from agent.auth import check_strict_auth
from agent.services.workflow_runtime_capability_service import (
    default_workflow_runtime_capability_service,
)
from agent.services.workflow_runtime_health_service import (
    default_workflow_runtime_health_service,
)

workflow_runtime_capabilities_bp = Blueprint(
    "workflow_runtime_capabilities",
    __name__,
    url_prefix="/api/workflow-runtime/capabilities",
)

_ALLOWED_QUERY_FIELDS = frozenset({"required_capability"})
_MAX_REQUIRED_CAPABILITIES = 64
_MAX_CAPABILITY_LENGTH = 128


@workflow_runtime_capabilities_bp.get("")
@workflow_runtime_capabilities_bp.get("/")
@check_strict_auth
def get_workflow_runtime_capabilities():
    """Return the single Hub-owned projection consumed by every UI surface."""

    if set(request.args).difference(_ALLOWED_QUERY_FIELDS):
        return jsonify({"status": "error", "reason_code": "runtime_capability_query_forbidden"}), 400
    required = tuple(
        str(value).strip()
        for value in request.args.getlist("required_capability")
        if str(value).strip()
    )
    if (
        len(required) > _MAX_REQUIRED_CAPABILITIES
        or any(len(value) > _MAX_CAPABILITY_LENGTH for value in required)
    ):
        return jsonify({"status": "error", "reason_code": "runtime_capability_query_invalid"}), 400
    try:
        projection = default_workflow_runtime_capability_service(
            health=default_workflow_runtime_health_service(),
        ).hub_projection(
            required_capabilities=required,
        )
    except (OSError, ValueError):
        # Configuration details are intentionally not disclosed to callers.
        return jsonify({"status": "error", "reason_code": "runtime_capability_matrix_unavailable"}), 503
    return jsonify(projection), 200


__all__ = ["workflow_runtime_capabilities_bp"]
