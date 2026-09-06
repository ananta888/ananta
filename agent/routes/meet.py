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
