"""Strict registered-Worker Native byte ingress; no generic artifact authority."""

from flask import Blueprint, g, request

from agent.auth import check_registered_worker_auth
from agent.common.audit import log_audit
from agent.common.errors import api_response
from agent.services.native_artifact_runtime import get_native_artifact_runtime
from agent.services.workflow_runtime.errors import WorkflowRuntimeError
from agent.services.workflow_worker_service_auth import NATIVE_ARTIFACT_INGRESS_SCOPE, NATIVE_ARTIFACT_INPUT_SCOPE
from agent.utils import rate_limit
from ananta_contracts.native_artifact_transfer import MAX_TRANSFER_HTTP_BYTES

native_artifact_ingress_bp = Blueprint("native_artifact_ingress", __name__)
MAX_HTTP_BYTES = MAX_TRANSFER_HTTP_BYTES


@native_artifact_ingress_bp.route("/api/internal/tasks/<task_id>/native-artifacts", methods=["GET", "POST"])
@native_artifact_ingress_bp.route("/internal/tasks/<task_id>/native-artifacts", methods=["GET", "POST"])
@rate_limit(limit=120, window=60, namespace="native_artifact_ingress")
@check_registered_worker_auth(scope=NATIVE_ARTIFACT_INGRESS_SCOPE)
def native_artifact_ingress(task_id):
    transfer = None
    if request.method == "POST":
        if not request.content_length or request.content_length > MAX_HTTP_BYTES:
            return api_response(status="error", message="native_artifact_http_size_invalid", code=413)
        if not request.is_json:
            return api_response(status="error", message="native_artifact_json_required", code=400)
        request.max_content_length = MAX_HTTP_BYTES
        transfer = request.get_json(silent=True)
        if not isinstance(transfer, dict):
            return api_response(status="error", message="native_artifact_transfer_invalid", code=400)
    identity = dict(getattr(g, "service_identity", {}) or {})
    try:
        result = get_native_artifact_runtime().execute(
            worker_id=identity.get("worker_id", ""), worker_url=identity.get("worker_url", ""),
            task_id=task_id, transfer=transfer,
        )
    except (ValueError, WorkflowRuntimeError) as exc:
        reason = str(exc)
        log_audit("native_artifact_ingress_denied", {"task_id": task_id, "reason_code": reason})
        return api_response(status="error", message=reason, data={"reason_code": reason}, code=409)
    log_audit("native_artifact_ingress_admitted", {"task_id": task_id, "method": request.method})
    return api_response(data=result)


@native_artifact_ingress_bp.get("/api/internal/tasks/<task_id>/native-artifact-inputs/<input_name>")
@native_artifact_ingress_bp.get("/internal/tasks/<task_id>/native-artifact-inputs/<input_name>")
@rate_limit(limit=120, window=60, namespace="native_artifact_inputs")
@check_registered_worker_auth(scope=NATIVE_ARTIFACT_INPUT_SCOPE)
def native_artifact_input(task_id, input_name):
    identity = dict(getattr(g, "service_identity", {}) or {})
    try:
        result = get_native_artifact_runtime().read_input(
            worker_id=identity.get("worker_id", ""), worker_url=identity.get("worker_url", ""),
            task_id=task_id, input_name=input_name,
        )
    except (ValueError, WorkflowRuntimeError) as exc:
        reason = str(exc)
        log_audit("native_artifact_input_denied", {"task_id": task_id, "input_name": input_name, "reason_code": reason})
        return api_response(status="error", message=reason, data={"reason_code": reason}, code=409)
    log_audit("native_artifact_input_admitted", {"task_id": task_id, "input_name": input_name})
    return api_response(data=result)
