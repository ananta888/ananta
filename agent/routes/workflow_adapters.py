"""Thin authenticated HTTP adapter over the Hub workflow-adapter control."""

from __future__ import annotations

import json
from typing import Any

from flask import Blueprint, Response, jsonify, request, stream_with_context
from werkzeug.exceptions import RequestEntityTooLarge

from agent.auth import check_strict_auth
from agent.common.audit import log_audit
from agent.common.errors import api_response
from agent.routes.workflow_control_security import workflow_principal
from agent.services.workflow_adapter_catalog_service import (
    workflow_adapter_catalog_service,
)
from agent.services.workflow_adapter_control_facade import (
    get_workflow_adapter_control_facade,
)
from agent.services.workflow_adapter_task_queue_service import (
    WorkflowAdapterQueueError,
)
from agent.services.workflow_runtime.streaming import WorkflowStreamError

workflow_adapters_bp = Blueprint(
    "workflow_adapters", __name__, url_prefix="/api/workflow_adapters"
)

_MAX_ADAPTER_COMMAND_BYTES = 64 * 1024
_MAX_ADAPTER_CANCEL_BYTES = 8 * 1024


@workflow_adapters_bp.get("/")
@check_strict_auth
def list_adapters():
    descriptors = workflow_adapter_catalog_service.list_descriptors()
    return jsonify({"adapters": descriptors, "count": len(descriptors)}), 200


@workflow_adapters_bp.get("/<kind>/")
@check_strict_auth
def get_adapter(kind: str):
    descriptor = workflow_adapter_catalog_service.get_descriptor(kind)
    if descriptor is None:
        return _error("workflow_adapter_unknown", code=404)
    return jsonify(descriptor), 200


@workflow_adapters_bp.post("/<kind>/dry_run")
@check_strict_auth
def dry_run(kind: str):
    return _submit(kind, command="dry_run")


@workflow_adapters_bp.post("/<kind>/execute")
@check_strict_auth
def execute(kind: str):
    return _submit(kind, command="execute")


@workflow_adapters_bp.get("/<kind>/operations/<hub_task_id>")
@check_strict_auth
def operation_status(kind: str, hub_task_id: str):
    unknown = _unknown_adapter(kind)
    if unknown is not None:
        return unknown
    try:
        control = _authorized_control()
        return jsonify(control.status(kind=kind, hub_task_id=hub_task_id)), 200
    except WorkflowAdapterQueueError as exc:
        return _queue_error(exc, kind=kind, operation="status")
    except Exception as exc:  # noqa: BLE001 - composition is fail-closed
        return _unavailable(exc, kind=kind, operation="status")


@workflow_adapters_bp.post("/<kind>/operations/<hub_task_id>/cancel")
@check_strict_auth
def cancel_operation(kind: str, hub_task_id: str):
    unknown = _unknown_adapter(kind)
    if unknown is not None:
        return unknown
    body, error = _json_body(max_bytes=_MAX_ADAPTER_CANCEL_BYTES, required=False)
    if error is not None:
        return error
    if set(body or {}) - {"reason"}:
        return _error("workflow_adapter_cancel_fields_forbidden", code=400)
    try:
        control = _authorized_control()
        payload = control.cancel(
            kind=kind,
            hub_task_id=hub_task_id,
            reason=str((body or {}).get("reason") or "workflow_adapter_cancelled"),
        )
        return jsonify(payload), 200
    except WorkflowAdapterQueueError as exc:
        return _queue_error(exc, kind=kind, operation="cancel")
    except Exception as exc:  # noqa: BLE001
        return _unavailable(exc, kind=kind, operation="cancel")


@workflow_adapters_bp.route("/<kind>/stream", methods=["GET", "POST"])
@check_strict_auth
def stream(kind: str):
    if request.method == "GET" or request.args:
        return _error("workflow_stream_query_transport_forbidden", code=400)
    unknown = _unknown_adapter(kind)
    if unknown is not None:
        return unknown
    body, error = _json_body(max_bytes=_MAX_ADAPTER_COMMAND_BYTES)
    if error is not None:
        return error
    try:
        control = _authorized_control()
        batch = control.stream(kind=kind, body=body or {})
    except WorkflowStreamError as exc:
        return _error(exc.reason_code, code=422)
    except WorkflowAdapterQueueError as exc:
        return _queue_error(exc, kind=kind, operation="stream")
    except Exception as exc:  # noqa: BLE001
        return _unavailable(exc, kind=kind, operation="stream")

    @stream_with_context
    def generate():
        for frame in batch.frames:
            yield (
                json.dumps(
                    frame.to_dict(),
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                    allow_nan=False,
                )
                + "\n"
            )

    response = Response(generate(), mimetype="application/x-ndjson")
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Workflow-Next-Cursor"] = batch.next_cursor
    response.headers["X-Workflow-Has-More"] = (
        "true" if batch.has_more else "false"
    )
    return response


def _submit(kind: str, *, command: str):
    unknown = _unknown_adapter(kind)
    if unknown is not None:
        return unknown
    if request.args:
        return _error("workflow_adapter_query_transport_forbidden", code=400)
    body, error = _json_body(max_bytes=_MAX_ADAPTER_COMMAND_BYTES)
    if error is not None:
        return error
    try:
        control = _authorized_control()
        payload = control.submit(
            kind=kind,
            command=command,
            body=body or {},
            idempotency_key=str(request.headers.get("Idempotency-Key") or ""),
        )
    except WorkflowAdapterQueueError as exc:
        return _queue_error(exc, kind=kind, operation=command)
    except ValueError as exc:
        reason = str(exc) if str(exc).startswith("workflow_") else "workflow_adapter_request_invalid"
        return _error(reason, code=422)
    except Exception as exc:  # noqa: BLE001 - missing Hub composition is unavailable
        return _unavailable(exc, kind=kind, operation=command)
    log_audit(
        "workflow_adapter_task_submitted",
        {
            "kind": str(kind).lower(),
            "command": command,
            "hub_task_id": str(payload.get("hub_task_id") or ""),
            "workflow_id": str(payload.get("workflow_id") or ""),
            "duplicate": bool(payload.get("duplicate", False)),
        },
    )
    return jsonify(payload), 202


def _authorized_control():
    try:
        principal = workflow_principal()
    except ValueError as exc:
        raise WorkflowAdapterQueueError(
            "workflow_adapter_principal_required", status_code=401
        ) from exc
    return get_workflow_adapter_control_facade().bind(principal)


def _unknown_adapter(kind: str):
    if workflow_adapter_catalog_service.get_descriptor(kind) is None:
        return _error("workflow_adapter_unknown", code=404)
    if str(kind or "").strip().lower() != "langgraph":
        return _error("workflow_adapter_kind_unsupported", code=422)
    return None


def _json_body(
    *, max_bytes: int, required: bool = True
) -> tuple[dict[str, Any] | None, object | None]:
    content_length = request.content_length
    if content_length is not None and content_length > max_bytes:
        return None, _error("workflow_adapter_payload_too_large", code=413)
    request.max_content_length = max_bytes
    try:
        raw = request.get_data(cache=True)
    except RequestEntityTooLarge:
        return None, _error("workflow_adapter_payload_too_large", code=413)
    if len(raw) > max_bytes:
        return None, _error("workflow_adapter_payload_too_large", code=413)
    if not raw and not required:
        return {}, None
    if not request.is_json:
        return None, _error("workflow_adapter_json_required", code=415)
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return None, _error("workflow_adapter_json_invalid", code=400)
    return body, None


def _queue_error(exc: WorkflowAdapterQueueError, *, kind: str, operation: str):
    log_audit(
        "workflow_adapter_control_rejected",
        {
            "kind": str(kind).lower(),
            "operation": operation,
            "reason_code": exc.reason_code,
            "http_status": exc.status_code,
        },
    )
    return _error(exc.reason_code, code=exc.status_code)


def _unavailable(exc: Exception, *, kind: str, operation: str):
    log_audit(
        "workflow_adapter_control_unavailable",
        {
            "kind": str(kind).lower(),
            "operation": operation,
            "exception_type": type(exc).__name__,
        },
    )
    return _error("workflow_adapter_control_unavailable", code=503, retryable=True)


def _error(reason_code: str, *, code: int, retryable: bool | None = None):
    return api_response(
        status="error",
        message=(
            "workflow adapter unavailable"
            if code >= 500
            else "workflow adapter request rejected"
        ),
        data={
            "reason_code": str(reason_code),
            "retryable": bool(code >= 500 if retryable is None else retryable),
        },
        code=code,
    )


__all__ = ["workflow_adapters_bp"]
