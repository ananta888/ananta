"""Workflow request, preflight, start, status and cancel endpoints."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from flask import jsonify

from agent.auth import check_strict_auth
from agent.common.audit import log_audit
from agent.common.errors import api_response
from agent.routes.visual_process_blueprint import vp_bp
from agent.routes.visual_process_graph_support import (
    _parse_graph,
    _validator,
    _visual_process_module,
)
from agent.routes.visual_process_model_plan import _compile_workflow_request
from agent.routes.visual_process_workflow_command_routes import (
    _public_command_rejection_reason,
    _workflow_command_id,
    _workflow_command_target_bindings,
)
from agent.services.bpmn_workflow_preflight import (
    assert_workflow_start_hashes,
    workflow_start_plan,
)
from agent.services.caseflow_agent_collaboration_trace_projection_service import CASEFLOW_EDGE_CATALOG_METADATA_KEY
from agent.services.workflow_backend import WorkflowRequest
from agent.services.workflow_control_command_receipts import WorkflowControlCommandRejectedError
from agent.visual_process.definition_snapshot_contract import VISUAL_PROCESS_DEFINITION_HASH_METADATA_KEY

from .workflow_control_security import (
    MAX_WORKFLOW_CANCEL_BYTES,
    MAX_WORKFLOW_REQUEST_BYTES,
    backend_result,
    validate_workflow_id,
    workflow_json_body,
    workflow_principal,
)

# ── Canonical workflow request / backend port ────────────────────────────────


@vp_bp.post("/workflow-request")
@check_strict_auth
def workflow_request():
    body, body_error = workflow_json_body(max_bytes=MAX_WORKFLOW_REQUEST_BYTES)
    if body_error is not None:
        return body_error
    assert body is not None
    graph, err = _parse_graph()
    if err:
        return jsonify(err), 400
    validation = _validator.validate(graph)
    if not validation.valid:
        return jsonify({"validation": validation.as_dict(), "error": "invalid_graph"}), 422
    from agent.visual_process.bpmn_execution_support import BpmnExecutionError

    try:
        workflow = _compile_workflow_request(graph, body)
    except BpmnExecutionError as exc:
        return jsonify(exc.as_dict()), 422
    errors = workflow.validate()
    return jsonify(
        {
            "workflow_request": workflow.to_dict(),
            "validation": validation.as_dict(),
            "errors": errors,
        }
    ), 200 if not errors else 422


def _workflow_start_request(body: dict[str, Any]):
    """Identical bounded source admission for start and its read-only preview."""
    _, command_id_error = _workflow_command_id(body.get("command_id"))
    if command_id_error is not None:
        return None, command_id_error
    workflow_body = dict(body)
    workflow_body.pop("command_id", None)
    if "workflow_request" in workflow_body:
        try:
            workflow = WorkflowRequest.from_mapping(workflow_body.get("workflow_request") or {})
        except Exception as exc:
            return None, (jsonify({"error": "invalid_workflow_request", "detail": str(exc)}), 400)
        errors = workflow.validate()
        if errors:
            return None, (jsonify({"error": "invalid_workflow_request", "errors": errors}), 422)
        # Canonical edge identity is Hub-derived from a validated graph. A
        # direct neutral WorkflowRequest may not assert that internal catalog.
        direct_metadata = dict(workflow.metadata)
        direct_metadata.pop(CASEFLOW_EDGE_CATALOG_METADATA_KEY, None)
        direct_metadata.pop(VISUAL_PROCESS_DEFINITION_HASH_METADATA_KEY, None)
        workflow = replace(workflow, metadata=direct_metadata)
    else:
        graph, err = _parse_graph()
        if err:
            return None, (jsonify(err), 400)
        validation = _validator.validate(graph)
        if not validation.valid:
            return None, (jsonify({"validation": validation.as_dict(), "error": "invalid_graph"}), 422)
        from agent.visual_process.bpmn_execution_support import BpmnExecutionError

        try:
            workflow = _compile_workflow_request(graph, workflow_body)
        except BpmnExecutionError as exc:
            return None, (jsonify(exc.as_dict()), 422)
        errors = workflow.validate()
        if errors:
            return None, (jsonify({"error": "invalid_workflow_request", "errors": errors}), 422)
    invalid_id = validate_workflow_id(workflow.workflow_id)
    if invalid_id is not None:
        return None, invalid_id
    return workflow, None


def _principal_workflow_request(workflow: WorkflowRequest, principal) -> WorkflowRequest:
    return replace(
        workflow,
        requested_by=principal.subject,
        metadata={
            **dict(workflow.metadata),
            "authorization_scope": {"tenant_id": principal.tenant_id, "subject": principal.subject},
        },
    )


@vp_bp.post("/workflow/preflight")
@check_strict_auth
def workflow_preflight():
    body, error = workflow_json_body(max_bytes=MAX_WORKFLOW_REQUEST_BYTES)
    if error is not None:
        return error
    workflow, error = _workflow_start_request(body)
    if error is not None:
        return error
    try:
        principal = workflow_principal()
    except ValueError:
        return _visual_process_module().backend_error("workflow_principal_required", code=401)
    backend, error = _visual_process_module().configured_workflow_backend(principal)
    if error is not None:
        return error
    try:
        result = backend.preflight_workflow(_principal_workflow_request(workflow, principal))
    except ValueError:
        return _visual_process_module().backend_error("workflow_execution_plan_invalid", code=422)
    except Exception as exc:
        log_audit("workflow_preflight_failed", {"exception_type": type(exc).__name__})
        return _visual_process_module().backend_error("workflow_preflight_unavailable", code=503)
    return jsonify(result), 200


@vp_bp.post("/workflow/start")
@check_strict_auth
def workflow_start():
    body, body_error = workflow_json_body(max_bytes=MAX_WORKFLOW_REQUEST_BYTES)
    if body_error is not None:
        return body_error
    workflow, error = _workflow_start_request(body)
    if error is not None:
        return error
    command_id, _ = _workflow_command_id(body.get("command_id"))
    try:
        principal = workflow_principal()
    except ValueError:
        return api_response(
            status="error",
            message="authenticated workflow principal required",
            data={"reason_code": "workflow_principal_required"},
            code=401,
        )
    workflow = _principal_workflow_request(workflow, principal)
    preconditions = {}
    for key in ("expected_plan_hash", "expected_definition_hash"):
        if key not in body:
            continue
        value = body[key]
        if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
            return _visual_process_module().backend_error("workflow_start_hash_invalid", code=422)
        preconditions[key] = value
    if preconditions:
        try:
            plan = workflow_start_plan(workflow, tenant_id=principal.tenant_id)
        except ValueError:
            return _visual_process_module().backend_error("workflow_execution_plan_invalid", code=422)
        try:
            assert_workflow_start_hashes(workflow, plan, **preconditions)
        except ValueError as exc:
            return _visual_process_module().backend_error(str(exc), code=409)
    backend, backend_failure = _visual_process_module().configured_workflow_backend(principal)
    if backend_failure is not None:
        return backend_failure
    reservation = _visual_process_module().workflow_route_authorization_service.reserve(workflow.workflow_id, principal)
    if reservation == "foreign":
        return api_response(
            status="error",
            message="workflow id unavailable",
            data={"reason_code": "workflow_id_unavailable"},
            code=409,
        )
    if reservation not in {"reserved", "duplicate"}:
        return _visual_process_module().backend_error("workflow_id_invalid", code=400)

    try:
        start_options = {**preconditions, **({"command_id": command_id} if command_id else {})}
        status = backend.start_workflow(workflow, **start_options)
    except Exception as exc:  # noqa: BLE001
        pending = str(exc) == "workflow_control_start_observation_pending"
        if not pending and reservation != "duplicate":
            _visual_process_module().workflow_route_authorization_service.release(workflow.workflow_id, principal)
        log_audit(
            ("workflow_control_start_observation_pending" if pending else "workflow_backend_start_failed"),
            {"workflow_id": workflow.workflow_id, "exception_type": type(exc).__name__},
        )
        if pending:
            try:
                pending_status = backend.get_workflow_status(workflow.workflow_id)
            except Exception:  # the persisted binding remains queryable on retry
                return _visual_process_module().backend_error(
                    "workflow_control_start_observation_pending",
                    code=503,
                )
            return backend_result(pending_status, success_code=202)
        return _visual_process_module().backend_error("workflow_backend_unavailable", code=503)
    if str(status.get("status") or "").lower() in {"degraded", "unavailable", "not_found"}:
        _visual_process_module().workflow_route_authorization_service.release(workflow.workflow_id, principal)
    return backend_result(status)


@vp_bp.get("/workflow/<workflow_id>/status")
@check_strict_auth
def workflow_status(workflow_id: str):
    principal, auth_error = _visual_process_module().require_workflow_owner(workflow_id)
    if auth_error is not None:
        return auth_error
    assert principal is not None
    backend, backend_failure = _visual_process_module().configured_workflow_backend(principal)
    if backend_failure is not None:
        return backend_failure
    try:
        status = backend.get_workflow_status(workflow_id)
    except Exception as exc:  # noqa: BLE001
        log_audit(
            "workflow_backend_status_failed",
            {"workflow_id": workflow_id, "exception_type": type(exc).__name__},
        )
        return _visual_process_module().backend_error("workflow_backend_unavailable", code=503)
    if str(status.get("status") or "").lower() == "not_found" and principal is not None:
        _visual_process_module().workflow_route_authorization_service.release(workflow_id, principal)
    return backend_result(status)


@vp_bp.post("/workflow/<workflow_id>/cancel")
@check_strict_auth
def workflow_cancel(workflow_id: str):
    principal, auth_error = _visual_process_module().require_workflow_owner(workflow_id)
    if auth_error is not None:
        return auth_error
    body, body_error = workflow_json_body(max_bytes=MAX_WORKFLOW_CANCEL_BYTES, required=False)
    if body_error is not None:
        return body_error
    assert body is not None
    target_bindings, binding_error = _workflow_command_target_bindings(body)
    if binding_error is not None:
        return binding_error
    expected_revision = body.get("expected_revision")
    if "expected_revision" in body and (type(expected_revision) is not int or expected_revision < 0):
        return _visual_process_module().backend_error("workflow_control_revision_invalid", code=422)
    reason = str(body.get("reason") or "").strip()
    command_id, command_id_error = _workflow_command_id(body.get("command_id"))
    if command_id_error is not None:
        return command_id_error
    if len(reason) > 1000:
        return api_response(
            status="error",
            message="cancel reason too long",
            data={"reason_code": "workflow_cancel_reason_too_long"},
            code=422,
        )
    assert principal is not None
    backend, backend_failure = _visual_process_module().configured_workflow_backend(principal)
    if backend_failure is not None:
        return backend_failure
    try:
        command = getattr(backend, "command_workflow", None)
        bindings = dict(target_bindings)
        if expected_revision is not None:
            bindings["expected_revision"] = expected_revision
        if body.get("plan_hash") is not None:
            bindings["plan_hash"] = body["plan_hash"]
        if bindings and not callable(command):
            return _visual_process_module().backend_error("workflow_control_command_unavailable", code=503)
        status = (
            command(
                workflow_id,
                command_type="cancel",
                payload={"reason": reason},
                command_id=command_id,
                **bindings,
            )
            if (command_id or bindings) and callable(command)
            else backend.cancel_workflow(workflow_id, reason=reason)
        )
    except WorkflowControlCommandRejectedError as exc:
        safe_reason = _public_command_rejection_reason(exc.reason_code)
        log_audit(
            "workflow_control_command_rejected",
            {"workflow_id": workflow_id, "reason_code": safe_reason},
        )
        return _visual_process_module().backend_error(safe_reason, code=409)
    except Exception as exc:  # noqa: BLE001
        log_audit(
            "workflow_backend_cancel_failed",
            {"workflow_id": workflow_id, "exception_type": type(exc).__name__},
        )
        if str(exc) == "workflow_control_command_observation_pending":
            try:
                pending_status = backend.get_workflow_status(workflow_id)
            except Exception:
                return _visual_process_module().backend_error(str(exc), code=503)
            return backend_result(pending_status, success_code=202)
        return _visual_process_module().backend_error("workflow_backend_unavailable", code=503)
    return backend_result(status)
