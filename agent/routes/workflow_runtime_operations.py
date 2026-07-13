"""Authenticated Hub API for workflow-runtime evaluation and operations."""

from __future__ import annotations

from typing import Any

from flask import Blueprint, jsonify, request
from werkzeug.exceptions import RequestEntityTooLarge

from agent.auth import check_strict_auth, get_request_auth_context
from agent.services.workflow_runtime_command_service import (
    RuntimeOperationCommandError,
    RuntimeOperationCommandRequest,
    get_workflow_runtime_command_service,
)
from agent.services.workflow_runtime_read_model_service import (
    RuntimeOperationsQuery,
    get_workflow_runtime_read_model_service,
)

workflow_runtime_operations_bp = Blueprint(
    "workflow_runtime_operations",
    __name__,
    url_prefix="/api/workflow-runtime/operations",
)

_MAX_COMMAND_BYTES = 16 * 1024
_COMMAND_FIELDS = frozenset({"type", "command_type", "approval_id", "evidence_refs", "idempotency_key"})


def _identity() -> tuple[str, str]:
    claims: dict[str, Any] = dict(get_request_auth_context() or {})
    actor = str(claims.get("sub") or claims.get("username") or "hub-operator").strip()[:160]
    tenant_id = str(
        claims.get("tenant_id")
        or claims.get("tenant")
        or claims.get("organization_id")
        or actor
    ).strip()[:160]
    # check_strict_auth guarantees a credential; a missing tenant claim is
    # explicitly scoped to the authenticated subject, never to a global tenant.
    return tenant_id, actor


@workflow_runtime_operations_bp.get("")
@workflow_runtime_operations_bp.get("/")
@check_strict_auth
def list_runtime_operations():
    tenant_id, _ = _identity()
    try:
        query = RuntimeOperationsQuery.from_mapping(request.args)
    except ValueError as exc:
        return jsonify({"status": "error", "reason_code": str(exc)}), 400
    payload = get_workflow_runtime_read_model_service().list_runs(
        tenant_id=tenant_id,
        query=query,
    )
    return jsonify(payload), 200


@workflow_runtime_operations_bp.get("/runs/<run_id>")
@check_strict_auth
def get_runtime_operation(run_id: str):
    tenant_id, _ = _identity()
    payload = get_workflow_runtime_read_model_service().get_run(
        tenant_id=tenant_id,
        run_id=str(run_id).strip(),
    )
    if payload is None:
        # A cross-tenant identifier is intentionally indistinguishable from a
        # missing run to avoid leaking tenant membership.
        return jsonify({"status": "error", "reason_code": "runtime_run_not_found"}), 404
    return jsonify({"status": "ok", "run": payload}), 200


@workflow_runtime_operations_bp.post("/runs/<run_id>/commands")
@check_strict_auth
def send_runtime_operation_command(run_id: str):
    content_length = request.content_length
    if content_length is not None and content_length > _MAX_COMMAND_BYTES:
        return jsonify({"status": "error", "reason_code": "runtime_command_payload_too_large"}), 413
    request.max_content_length = _MAX_COMMAND_BYTES
    try:
        raw_body = request.get_data(cache=True)
    except RequestEntityTooLarge:
        return jsonify({"status": "error", "reason_code": "runtime_command_payload_too_large"}), 413
    if len(raw_body) > _MAX_COMMAND_BYTES:
        return jsonify({"status": "error", "reason_code": "runtime_command_payload_too_large"}), 413
    if not request.is_json:
        return jsonify({"status": "error", "reason_code": "runtime_command_json_required"}), 415
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"status": "error", "reason_code": "runtime_command_json_invalid"}), 400
    if set(body).difference(_COMMAND_FIELDS):
        return jsonify({"status": "error", "reason_code": "runtime_command_fields_forbidden"}), 400

    tenant_id, actor = _identity()
    try:
        command_request = RuntimeOperationCommandRequest.from_mapping(
            body,
            idempotency_key=str(request.headers.get("Idempotency-Key") or ""),
        )
        command = get_workflow_runtime_command_service().dispatch(
            tenant_id=tenant_id,
            run_id=str(run_id).strip(),
            actor=actor,
            request=command_request,
        )
    except RuntimeOperationCommandError as exc:
        return jsonify({"status": "error", "reason_code": exc.reason_code}), exc.http_status
    command_status = str(command.get("status") or "")
    if command_status == "rejected_by_policy":
        return jsonify({
            "status": "error",
            "reason_code": "runtime_command_rejected_by_hub_policy",
            "command": command,
        }), 422
    if command_status == "failed":
        return jsonify({
            "status": "error",
            "reason_code": "runtime_command_hub_dispatch_failed",
            "command": command,
        }), 502
    return jsonify({"status": "ok", "command": command}), 202


__all__ = ["workflow_runtime_operations_bp"]
