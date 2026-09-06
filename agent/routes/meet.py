"""User-only API; no service token can operate the meeting association."""

import re

from flask import Blueprint, current_app, jsonify, request

from agent.auth import check_user_auth, get_authenticated_source_control_principal
from agent.services.meet_contract import MeetError
from agent.services.project_access_authority import ProjectAccessError
from agent.services.task_read_access_service import TaskReadAccessError

meet_bp = Blueprint("meet", __name__, url_prefix="/api/meet/v1")


@meet_bp.before_request
def validate_auth_header_shape():
    # Bound malformed bearer input before the shared user-auth decorator.
    header = request.headers.get("Authorization")
    if header is not None and (len(header) > 8192 or not re.fullmatch(r"Bearer [A-Za-z0-9._~-]+", header)):
        raise MeetError("meet_auth_header_invalid", 401)


@meet_bp.after_request
def no_store(response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@meet_bp.errorhandler(MeetError)
def meet_error(exc):
    return jsonify({"error": {"code": exc.code}}), exc.status


@meet_bp.errorhandler(ProjectAccessError)
def project_error(exc):
    return jsonify({"error": {"code": exc.reason_code}}), exc.public_status


@meet_bp.errorhandler(TaskReadAccessError)
def task_error(exc):
    return jsonify({"error": {"code": exc.reason_code}}), exc.status_code


def _runtime():
    if current_app.config.get("ROLE") != "hub":
        raise MeetError("meet_hub_required", 403)
    runtime = current_app.extensions.get("meet_binding_service")
    if runtime is None:
        raise MeetError("meet_disabled", 404)
    return runtime


@meet_bp.route("/projects/<project>/binding", methods=["GET", "PUT", "DELETE"])
@meet_bp.route("/projects/<project>/tasks/<task>/binding", methods=["GET", "PUT", "DELETE"])
@check_user_auth
def binding(project, task=""):
    runtime = _runtime()
    if request.args:
        raise MeetError("meet_query_invalid")
    principal = get_authenticated_source_control_principal()
    if request.method == "GET":
        return jsonify(runtime.read(principal, project, task))
    if request.content_length is None or request.content_length > 2048:
        raise MeetError("meet_payload_too_large", 413)
    return jsonify(
        runtime.change(principal, project, task, request.get_json(silent=True), unlink=request.method == "DELETE")
    )


@meet_bp.get("/projects/<project>/health")
@check_user_auth
def health(project):
    runtime = _runtime()
    if request.args:
        raise MeetError("meet_query_invalid")
    runtime.read(get_authenticated_source_control_principal(), project)
    return jsonify(current_app.extensions["meet_health_probe"].inspect())


@meet_bp.post("/projects/<project>/turns")
@meet_bp.post("/projects/<project>/tasks/<task>/turns")
@check_user_auth
def media_turn(project, task=""):
    _runtime()
    runtime = current_app.extensions.get("meet_turn_service")
    if runtime is None:
        raise MeetError("meet_media_disabled", 404)
    if request.args or request.content_length is None or request.content_length > 10_000:
        raise MeetError("meet_turn_payload_invalid")
    return jsonify(
        runtime.execute(get_authenticated_source_control_principal(), project, request.get_json(silent=True), task=task)
    )


@meet_bp.post("/internal/lease")
def media_lease():
    """Read-only worker capability endpoint; never accepts a user/service JWT."""
    import json

    from ananta_contracts.meet_lease import lease_response_signature, validate_lease_request
    from worker.meet_media.contract import authenticate

    _runtime()
    runtime = current_app.extensions.get("meet_turn_service")
    key = current_app.extensions.get("meet_media_worker_key")
    if runtime is None or key is None:
        raise MeetError("meet_media_disabled", 404)
    if (
        request.args
        or request.headers.get("Transfer-Encoding")
        or request.content_length is None
        or not 0 < request.content_length <= 512
    ):
        raise MeetError("meet_lease_invalid")
    raw = request.get_data(cache=False)
    try:
        authenticate(key, raw, request.headers.get("X-Ananta-Task-Signature", ""))
        payload = json.loads(raw)
    except (ValueError, TypeError):
        raise MeetError("meet_lease_unauthorized", 401) from None
    if isinstance(payload, dict) and set(payload) == {"task_id", "lease_id"}:
        # V1 signed only {allowed:true}; a recorded result could authorize a
        # different/revoked lease. Never silently downgrade to that protocol.
        raise MeetError("meet_lease_protocol_upgrade_required", 409)
    try:
        validate_lease_request(payload)
    except ValueError:
        raise MeetError("meet_lease_unauthorized", 401) from None
    response = jsonify({"allowed": runtime.lease_allowed(payload["task_id"], payload["lease_id"])})
    response.headers["X-Ananta-Lease-Protocol"] = "ananta.meet-lease.v2"
    response.headers["X-Ananta-Lease-Signature"] = lease_response_signature(key, raw, response.get_data())
    return response
