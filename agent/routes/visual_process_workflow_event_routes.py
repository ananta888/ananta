"""Workflow event read, event stream and caseflow edge-trace endpoints."""

from __future__ import annotations

import json

from flask import (
    Response,
    jsonify,
    request,
    stream_with_context,
)

from agent.auth import check_strict_auth
from agent.common.audit import log_audit
from agent.common.errors import api_response
from agent.common.redaction import (
    VisibilityLevel,
    redact,
)
from agent.routes.visual_process_blueprint import vp_bp
from agent.routes.visual_process_graph_support import _visual_process_module
from agent.services.caseflow_agent_collaboration_trace_projection_service import (
    MAX_CASEFLOW_EDGE_TRACE_QUERY_BYTES,
    CaseflowEdgeTraceProjectionError,
    CaseflowEdgeTraceQuery,
)
from agent.services.workflow_runtime.streaming import (
    WorkflowStreamError,
    WorkflowStreamRequest,
    WorkflowStreamService,
)

from .workflow_control_security import (
    backend_result,
    workflow_json_body,
)


@vp_bp.get("/workflow/<workflow_id>/events")
@check_strict_auth
def workflow_events(workflow_id: str):
    principal, auth_error = _visual_process_module().require_workflow_owner(workflow_id)
    if auth_error is not None:
        return auth_error
    assert principal is not None
    backend, backend_failure = _visual_process_module().configured_workflow_backend(principal)
    if backend_failure is not None:
        return backend_failure
    try:
        status = backend.get_workflow_status(workflow_id)
        if str(status.get("status") or "").lower() in {"degraded", "unavailable", "not_found"}:
            return backend_result(status)
        events = backend.list_workflow_events(workflow_id)
    except Exception as exc:  # noqa: BLE001
        log_audit(
            "workflow_backend_events_failed",
            {"workflow_id": workflow_id, "exception_type": type(exc).__name__},
        )
        return _visual_process_module().backend_error("workflow_backend_unavailable", code=503)
    safe_events = [dict(redact(event, VisibilityLevel.USER) or {}) for event in events if isinstance(event, dict)]
    return jsonify({"events": safe_events}), 200


@vp_bp.post("/workflow/<workflow_id>/caseflow-edge-trace")
@check_strict_auth
def caseflow_edge_trace(workflow_id: str):
    """Project bounded directional edge evidence from existing Hub history."""

    if request.args:
        return api_response(
            status="error",
            message="caseflow edge trace parameters must not be sent in a URL",
            data={"reason_code": "caseflow_edge_trace_query_transport_forbidden"},
            code=400,
        )
    principal, auth_error = _visual_process_module().require_workflow_owner(workflow_id)
    if auth_error is not None:
        return auth_error
    body, body_error = workflow_json_body(max_bytes=MAX_CASEFLOW_EDGE_TRACE_QUERY_BYTES)
    if body_error is not None:
        return body_error
    try:
        query = CaseflowEdgeTraceQuery.from_mapping(body or {})
    except CaseflowEdgeTraceProjectionError as exc:
        return api_response(
            status="error",
            message="invalid caseflow edge trace request",
            data={"reason_code": exc.reason_code},
            code=exc.status_code,
        )
    assert principal is not None
    backend, backend_failure = _visual_process_module().configured_workflow_backend(principal)
    if backend_failure is not None:
        return backend_failure
    try:
        projection = _visual_process_module().get_caseflow_agent_collaboration_trace_projection_service().read(
            principal=principal,
            workflow_id=workflow_id,
            run_id=query.run_id,
            history=backend,
        )
    except CaseflowEdgeTraceProjectionError as exc:
        return api_response(
            status="error",
            message=("workflow not found" if exc.status_code == 404 else "caseflow edge trace unavailable"),
            data={"reason_code": exc.reason_code},
            code=exc.status_code,
        )
    except Exception as exc:  # noqa: BLE001
        log_audit(
            "caseflow_edge_trace_projection_failed",
            {
                "workflow_id": workflow_id,
                "exception_type": type(exc).__name__,
            },
        )
        return _visual_process_module().backend_error("caseflow_edge_trace_unavailable", code=503)
    return jsonify(projection), 200


@vp_bp.post("/workflow/events/stream")
@check_strict_auth
def workflow_event_stream():
    """Return a bounded, cursor-resumable NDJSON page from the Hub stream."""

    if request.args:
        return api_response(
            status="error",
            message="workflow stream parameters must not be sent in a URL",
            data={"reason_code": "workflow_stream_query_transport_forbidden"},
            code=400,
        )
    body, body_error = workflow_json_body(max_bytes=8 * 1024)
    if body_error is not None:
        return body_error
    try:
        stream_request = WorkflowStreamRequest.from_mapping(body or {})
    except WorkflowStreamError as exc:
        return api_response(
            status="error",
            message="invalid workflow stream request",
            data={"reason_code": exc.reason_code},
            code=422,
        )
    principal, auth_error = _visual_process_module().require_workflow_owner(stream_request.workflow_id)
    if auth_error is not None:
        return auth_error
    assert principal is not None
    backend, backend_failure = _visual_process_module().configured_workflow_backend(principal)
    if backend_failure is not None:
        return backend_failure
    try:
        status = backend.get_workflow_status(stream_request.workflow_id)
        if str(status.get("status") or "").lower() in {"degraded", "unavailable", "not_found"}:
            return backend_result(status)
        batch = WorkflowStreamService(backend).read(stream_request)
    except WorkflowStreamError as exc:
        return api_response(
            status="error",
            message="workflow stream cursor rejected",
            data={"reason_code": exc.reason_code},
            code=409,
        )
    except Exception as exc:  # noqa: BLE001
        log_audit(
            "workflow_stream_failed",
            {
                "workflow_id": stream_request.workflow_id,
                "exception_type": type(exc).__name__,
            },
        )
        return _visual_process_module().backend_error("workflow_stream_unavailable", code=503)

    log_audit(
        "workflow_stream_opened",
        {
            "workflow_id": stream_request.workflow_id,
            "after_cursor": stream_request.after_cursor,
            "frame_count": len(batch.frames),
        },
    )

    @stream_with_context
    def generate():
        try:
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
        finally:
            # A disconnected client reaches this path as GeneratorExit; the
            # cursor makes reconnect safe and no worker execution is affected.
            log_audit(
                "workflow_stream_closed",
                {
                    "workflow_id": stream_request.workflow_id,
                    "next_cursor": batch.next_cursor,
                },
            )

    response = Response(generate(), mimetype="application/x-ndjson")
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Workflow-Next-Cursor"] = batch.next_cursor
    response.headers["X-Workflow-Has-More"] = "true" if batch.has_more else "false"
    return response
