"""Workflow signal, BPMN message, resume and retry command endpoints."""

from __future__ import annotations

from typing import Any

from agent.auth import check_strict_auth
from agent.common.audit import log_audit
from agent.common.errors import api_response
from agent.common.redaction import (
    VisibilityLevel,
    redact,
)
from agent.routes.visual_process_blueprint import vp_bp
from agent.routes.visual_process_graph_support import _visual_process_module
from agent.services.workflow_backend import WorkflowSignal
from agent.services.workflow_control_command_receipts import WorkflowControlCommandRejectedError
from agent.services.workflow_runtime._serialization import redact_json
from agent.services.workflow_runtime.commands import validate_bpmn_message_payload

from .workflow_control_security import (
    MAX_WORKFLOW_SIGNAL_BYTES,
    backend_result,
    workflow_json_body,
)


@vp_bp.post("/workflow/<workflow_id>/signal")
@check_strict_auth
def workflow_signal(workflow_id: str):
    principal, auth_error = _visual_process_module().require_workflow_owner(workflow_id)
    if auth_error is not None:
        return auth_error
    body, body_error = workflow_json_body(max_bytes=MAX_WORKFLOW_SIGNAL_BYTES)
    if body_error is not None:
        return body_error
    assert body is not None and principal is not None
    if body.get("name") == "bpmn_message":
        return _visual_process_module().backend_error("bpmn_message_endpoint_required", code=422)
    if "expected_revision" in body and (type(body["expected_revision"]) is not int or body["expected_revision"] < 0):
        return _visual_process_module().backend_error("workflow_control_revision_invalid", code=422)
    target_bindings, binding_error = _workflow_command_target_bindings(body)
    if binding_error is not None:
        return binding_error
    if not isinstance(body.get("payload", {}), dict):
        return api_response(
            status="error",
            message="workflow signal payload must be an object",
            data={"reason_code": "workflow_signal_payload_invalid"},
            code=422,
        )
    signal = WorkflowSignal.from_mapping(
        {
            **body,
            "payload": redact_json(redact(dict(body.get("payload") or {}), VisibilityLevel.PUBLIC)),
            "actor": principal.subject,
        }
    )
    if not signal.name:
        return api_response(
            status="error",
            message="signal name required",
            data={"reason_code": "workflow_signal_name_required"},
            code=400,
        )
    if len(signal.name) > 64 or not all(character.isalnum() or character in "._-" for character in signal.name):
        return api_response(
            status="error",
            message="invalid signal name",
            data={"reason_code": "workflow_signal_name_invalid"},
            code=422,
        )
    command_id, command_id_error = _workflow_command_id(body.get("command_id"))
    if command_id_error is not None:
        return command_id_error
    return _dispatch_workflow_signal(
        workflow_id,
        principal,
        signal,
        command_id=command_id,
        expected_revision=body.get("expected_revision"),
        plan_hash=body.get("plan_hash"),
        **target_bindings,
    )


@vp_bp.post("/workflow/<workflow_id>/message")
@check_strict_auth
def workflow_message(workflow_id: str):
    principal, error = _visual_process_module().require_workflow_owner(workflow_id)
    if error is not None:
        return error
    body, error = workflow_json_body(max_bytes=MAX_WORKFLOW_SIGNAL_BYTES)
    if error is not None:
        return error
    required = {"command_id", "expected_revision", "plan_hash", "step_id", "payload"}
    if not required.issubset(body) or set(body) - required - {"run_id", "checkpoint_ref"}:
        return _visual_process_module().backend_error("bpmn_message_envelope_invalid", code=422)
    target_bindings, error = _workflow_command_target_bindings(body)
    if error is not None:
        return error
    command_id, error = _workflow_command_id(body["command_id"])
    if error is not None:
        return error
    if not command_id:
        return _visual_process_module().backend_error("bpmn_message_command_binding_required", code=422)
    if type(body["expected_revision"]) is not int or body["expected_revision"] < 0:
        return _visual_process_module().backend_error("workflow_control_revision_invalid", code=422)
    if not isinstance(body["step_id"], str) or not body["step_id"] or body["step_id"] != body["step_id"].strip():
        return _visual_process_module().backend_error("bpmn_message_target_required", code=422)
    plan_hash = body["plan_hash"]
    if not isinstance(plan_hash, str) or len(plan_hash) != 64 or any(c not in "0123456789abcdef" for c in plan_hash):
        return _visual_process_module().backend_error("workflow_control_plan_binding_mismatch", code=422)
    try:
        validate_bpmn_message_payload(body["payload"])
    except (ValueError, TypeError, OverflowError):
        return _visual_process_module().backend_error("bpmn_message_payload_invalid", code=422)
    return _dispatch_workflow_signal(
        workflow_id, principal,
        WorkflowSignal(name="bpmn_message", payload=body["payload"], actor=principal.subject),
        command_id=command_id, expected_revision=body["expected_revision"],
        plan_hash=plan_hash, step_id=body["step_id"],
        **target_bindings,
    )


@vp_bp.post("/workflow/<workflow_id>/resume")
@check_strict_auth
def workflow_resume(workflow_id: str):
    return _named_workflow_control(workflow_id, "resume")


@vp_bp.post("/workflow/<workflow_id>/retry")
@check_strict_auth
def workflow_retry(workflow_id: str):
    return _named_workflow_control(workflow_id, "retry")


def _named_workflow_control(workflow_id: str, command_name: str):
    principal, auth_error = _visual_process_module().require_workflow_owner(workflow_id)
    if auth_error is not None:
        return auth_error
    body, body_error = workflow_json_body(max_bytes=MAX_WORKFLOW_SIGNAL_BYTES, required=False)
    if body_error is not None:
        return body_error
    assert body is not None and principal is not None
    if "expected_revision" in body and (type(body["expected_revision"]) is not int or body["expected_revision"] < 0):
        return _visual_process_module().backend_error("workflow_control_revision_invalid", code=422)
    target_bindings, binding_error = _workflow_command_target_bindings(body)
    if binding_error is not None:
        return binding_error
    command_id, command_id_error = _workflow_command_id(body.get("command_id"))
    if command_id_error is not None:
        return command_id_error
    payload = body.get("payload", body)
    if not isinstance(payload, dict):
        return api_response(
            status="error",
            message="workflow control payload must be an object",
            data={"reason_code": "workflow_signal_payload_invalid"},
            code=422,
        )
    safe_payload = dict(payload)
    safe_payload.pop("command_id", None)
    safe_payload.pop("expected_revision", None)
    safe_payload.pop("plan_hash", None)
    signal = WorkflowSignal(
        name=command_name,
        payload=dict(redact_json(redact(safe_payload, VisibilityLevel.PUBLIC)) or {}),
        actor=principal.subject,
    )
    return _dispatch_workflow_signal(
        workflow_id,
        principal,
        signal,
        command_id=command_id,
        expected_revision=body.get("expected_revision"),
        plan_hash=body.get("plan_hash"),
        **target_bindings,
    )


def _workflow_command_target_bindings(body: dict[str, Any]):
    """Accept the established nested control form and the top-level envelope."""
    nested = body.get("payload")
    nested = nested if isinstance(nested, dict) else {}
    bindings = {}
    for key in ("run_id", "checkpoint_ref"):
        if key not in body and key not in nested:
            continue
        if key in body and key in nested and body[key] != nested[key]:
            return None, _visual_process_module().backend_error("workflow_control_target_binding_conflict", code=422)
        value = body[key] if key in body else nested[key]
        if not isinstance(value, str) or not value or value != value.strip() or len(value) > 512:
            return None, _visual_process_module().backend_error("workflow_control_target_binding_invalid", code=422)
        bindings[key] = value
    return bindings, None


def _dispatch_workflow_signal(
    workflow_id: str,
    principal,
    signal: WorkflowSignal,
    *,
    command_id: str = "",
    expected_revision: int | None = None,
    plan_hash: str | None = None,
    step_id: str | None = None,
    run_id: str | None = None,
    checkpoint_ref: str | None = None,
):
    if expected_revision is not None and (type(expected_revision) is not int or expected_revision < 0):
        return _visual_process_module().backend_error("workflow_control_revision_invalid", code=422)
    backend, backend_failure = _visual_process_module().configured_workflow_backend(principal)
    if backend_failure is not None:
        return backend_failure
    try:
        command = getattr(backend, "command_workflow", None)
        bindings = {}
        if expected_revision is not None:
            bindings["expected_revision"] = expected_revision
        if plan_hash is not None:
            bindings["plan_hash"] = plan_hash
        if step_id is not None:
            bindings["step_id"] = step_id
        if run_id is not None:
            bindings["run_id"] = run_id
        if checkpoint_ref is not None:
            bindings["checkpoint_ref"] = checkpoint_ref
        if (bindings or signal.name == "bpmn_message") and not callable(command):
            return _visual_process_module().backend_error("workflow_control_command_unavailable", code=503)
        status = (
            command(
                workflow_id,
                command_type=signal.name,
                payload=dict(signal.payload),
                command_id=command_id,
                **bindings,
            )
            if (command_id or bindings) and callable(command)
            else backend.signal_workflow(workflow_id, signal)
        )
    except WorkflowControlCommandRejectedError as exc:
        safe_reason = _public_command_rejection_reason(exc.reason_code)
        log_audit(
            "workflow_control_command_rejected",
            {"workflow_id": workflow_id, "reason_code": safe_reason},
        )
        return _visual_process_module().backend_error(safe_reason, code=409)
    except PermissionError as exc:
        reason_code = str(exc)
        safe_reason = (
            reason_code
            if reason_code
            in {
                "temporal_hub_verified_command_required",
                "workflow_control_checkpoint_binding_mismatch",
                "workflow_control_plan_binding_mismatch",
                "workflow_control_policy_binding_mismatch",
                "workflow_control_principal_binding_mismatch",
                "workflow_control_run_binding_mismatch",
            }
            else "workflow_control_command_denied"
        )
        log_audit(
            "workflow_control_command_denied",
            {"workflow_id": workflow_id, "reason_code": safe_reason},
        )
        return _visual_process_module().backend_error(safe_reason, code=409)
    except Exception as exc:  # noqa: BLE001
        log_audit(
            "workflow_backend_signal_failed",
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


def _workflow_command_id(raw: Any):
    if raw is None or raw == "":
        return "", None
    if (
        not isinstance(raw, str)
        or raw != raw.strip()
        or len(raw) > 256
        or not raw
        or not raw[0].isalnum()
        or any(not character.isalnum() and character not in "_.:/-" for character in raw)
    ):
        return "", api_response(
            status="error",
            message="invalid workflow command id",
            data={"reason_code": "workflow_command_id_invalid"},
            code=422,
        )
    return raw, None


def _public_command_rejection_reason(reason_code: str) -> str:
    allowed = {
        "bpmn_message_command_binding_required",
        "bpmn_message_payload_invalid",
        "bpmn_message_target_required",
        "bpmn_message_target_invalid",
        "workflow_control_revision_invalid",
        "approval_gate_not_open",
        "authorization_binding_mismatch",
        "command_expired",
        "command_limit_exceeded",
        "langgraph_approval_gate_not_open",
        "langgraph_approval_gate_role_denied",
        "stale_workflow_revision",
        "temporal_retry_unsupported",
        "temporal_plan_edit_unsupported",
        "workflow_plan_edit_rebind_required",
        "workflow_control_command_id_conflict",
        "workflow_control_checkpoint_binding_mismatch",
        "workflow_control_command_rejected",
        "workflow_control_plan_binding_mismatch",
        "workflow_control_policy_binding_mismatch",
        "workflow_control_principal_binding_mismatch",
        "workflow_control_run_binding_mismatch",
        "workflow_not_pausable",
        "workflow_not_paused",
        "workflow_terminal",
    }
    normalized = str(reason_code or "").strip()
    return normalized if normalized in allowed else "workflow_control_command_rejected"
