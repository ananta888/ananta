"""Strict internal API for Temporal Activities and delegated workflow workers."""

from __future__ import annotations

from flask import Blueprint, jsonify, request
from werkzeug.exceptions import RequestEntityTooLarge

from agent.auth import check_strict_auth
from agent.services.workflow_hub_task_gateway_runtime import (
    WorkflowHubTaskConfigurationError,
    get_workflow_hub_task_gateway_service,
)
from agent.services.workflow_hub_task_gateway_service import WorkflowHubTaskError
from agent.services.workflow_worker_gateway_runtime import (
    get_workflow_worker_gateway_service,
)
from agent.services.workflow_worker_gateway_service import WorkflowWorkerGatewayError
from ananta_contracts.hub_task_gateway import HUB_TASK_COMMAND_SCHEMA

workflow_runtime_internal_bp = Blueprint(
    "workflow_runtime_internal",
    __name__,
    url_prefix="/api/internal/workflow-runtime",
)
_MAX_BODY_BYTES = 262_144


def _body() -> dict:
    content_length = request.content_length
    if content_length is not None and content_length > _MAX_BODY_BYTES:
        raise WorkflowHubTaskError("workflow_hub_task_payload_too_large", status_code=413)
    request.max_content_length = _MAX_BODY_BYTES
    try:
        raw = request.get_data(cache=True)
    except RequestEntityTooLarge as exc:
        raise WorkflowHubTaskError("workflow_hub_task_payload_too_large", status_code=413) from exc
    if len(raw) > _MAX_BODY_BYTES:
        raise WorkflowHubTaskError("workflow_hub_task_payload_too_large", status_code=413)
    value = request.get_json(silent=True)
    if not isinstance(value, dict):
        raise WorkflowHubTaskError("workflow_hub_task_json_required", status_code=400)
    return value


def _service():
    try:
        return get_workflow_hub_task_gateway_service()
    except WorkflowHubTaskConfigurationError as exc:
        raise WorkflowHubTaskError("workflow_hub_task_gateway_unavailable", status_code=503) from exc


def _error(exc: WorkflowHubTaskError):
    return (
        jsonify(
            {
                "status": "error",
                "message": "workflow runtime task request failed",
                "data": {"reason_code": exc.reason_code},
            }
        ),
        exc.status_code,
    )


def _worker_error(exc: WorkflowWorkerGatewayError):
    return (
        jsonify(
            {
                "status": "error",
                "message": "workflow worker decision failed",
                "data": {"reason_code": exc.reason_code},
            }
        ),
        exc.status_code,
    )


@workflow_runtime_internal_bp.post("/tasks")
@check_strict_auth
def submit_workflow_task():
    try:
        return jsonify({"data": _service().submit(_body())}), 202
    except WorkflowHubTaskError as exc:
        return _error(exc)


@workflow_runtime_internal_bp.post("/retries")
@check_strict_auth
def consume_workflow_retry():
    try:
        return jsonify({"data": _service().consume_retry(_body())}), 200
    except WorkflowHubTaskError as exc:
        return _error(exc)


@workflow_runtime_internal_bp.post("/worker-commands")
@check_strict_auth
def execute_workflow_worker_command():
    """Return one Hub-owned decision; never create or route worker tasks."""

    try:
        body = _body()
        return jsonify({"data": get_workflow_worker_gateway_service().execute(body)}), 200
    except WorkflowWorkerGatewayError as exc:
        return _worker_error(exc)
    except WorkflowHubTaskError as exc:
        return _error(exc)


@workflow_runtime_internal_bp.get("/tasks/<hub_task_id>")
@check_strict_auth
def get_workflow_task(hub_task_id: str):
    try:
        operation_id = str(request.args.get("operation_id") or "").strip()
        if not operation_id:
            raise WorkflowHubTaskError("workflow_operation_id_required", status_code=400)
        return jsonify({"data": _service().get(hub_task_id=hub_task_id, operation_id=operation_id)}), 200
    except WorkflowHubTaskError as exc:
        return _error(exc)


@workflow_runtime_internal_bp.get("/tasks/<hub_task_id>/payload")
@check_strict_auth
def get_workflow_task_payload(hub_task_id: str):
    try:
        operation_id = str(request.args.get("operation_id") or "").strip()
        if not operation_id:
            raise WorkflowHubTaskError("workflow_operation_id_required", status_code=400)
        data = _service().dispatch_payload(hub_task_id=hub_task_id, operation_id=operation_id)
        return jsonify({"data": data}), 200
    except WorkflowHubTaskError as exc:
        return _error(exc)


@workflow_runtime_internal_bp.post("/tasks/<hub_task_id>/commands")
@check_strict_auth
def command_workflow_task(hub_task_id: str):
    try:
        body = _body()
        if body.get("schema") != HUB_TASK_COMMAND_SCHEMA:
            raise WorkflowHubTaskError("workflow_hub_task_command_invalid", status_code=400)
        command = str(body.get("command") or "")
        if command == "cancel":
            data = _service().cancel(
                hub_task_id=hub_task_id,
                operation_id=str(body.get("operation_id") or ""),
                reason=str(body.get("reason") or "workflow_cancelled"),
            )
        elif command == "result":
            data = _service().finish(hub_task_id=hub_task_id, command=body)
        elif command == "status":
            data = _service().get(
                hub_task_id=hub_task_id,
                operation_id=str(body.get("operation_id") or ""),
            )
        elif command == "payload":
            data = _service().dispatch_payload(
                hub_task_id=hub_task_id,
                operation_id=str(body.get("operation_id") or ""),
            )
        else:
            raise WorkflowHubTaskError("workflow_hub_task_command_unsupported", status_code=422)
        return jsonify({"data": data}), 200
    except WorkflowHubTaskError as exc:
        return _error(exc)
