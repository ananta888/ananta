from __future__ import annotations

from flask import Blueprint, request

from agent.auth import admin_required, check_auth
from agent.common.errors import api_response
from agent.services.docker_compose_service import get_docker_compose_service
from agent.services.docker_engine_service import get_docker_engine_service
from agent.services.git_ops_service import get_git_ops_service
from agent.services.ops_models import OpsActionResult, OpsError


ops_bp = Blueprint("ops", __name__)


def _status_for_error(error: OpsError | None) -> int:
    if error is None:
        return 200
    if error.code in {"workspace_not_allowed", "path_not_allowed", "compose_project_not_registered"}:
        return 403
    if error.code in {"approval_required"}:
        return 409
    if error.code in {"policy_denied", "docker_boundary_not_configured"}:
        return 403
    if error.code in {"git_not_found", "docker_not_found", "compose_plugin_missing"}:
        return 503
    return 400


def _action_response(result: OpsActionResult):
    status = "success" if result.ok else "error"
    return api_response(data=result.to_dict(), status=status, message=result.error.code if result.error else None, code=_status_for_error(result.error))


@ops_bp.route("/git/status", methods=["GET"])
@check_auth
def git_status():
    result = get_git_ops_service().status(request.args.get("workspace_id"))
    status = "error" if result.error else "success"
    return api_response(data=result.to_dict(), status=status, message=result.error.code if result.error else None, code=_status_for_error(result.error))


@ops_bp.route("/git/diff", methods=["GET"])
@check_auth
def git_diff():
    cached = str(request.args.get("cached") or "").lower() in {"1", "true", "yes", "on"}
    result = get_git_ops_service().diff(request.args.get("workspace_id"), path=request.args.get("path"), cached=cached)
    status = "error" if result.error else "success"
    return api_response(data=result.to_dict(), status=status, message=result.error.code if result.error else None, code=_status_for_error(result.error))


@ops_bp.route("/git/stage", methods=["POST"])
@admin_required
def git_stage():
    data = request.get_json(silent=True) or {}
    result = get_git_ops_service().stage(data.get("workspace_id"), data.get("paths") or [], staged=not bool(data.get("unstage")))
    return _action_response(result)


@ops_bp.route("/git/commit", methods=["POST"])
@admin_required
def git_commit():
    data = request.get_json(silent=True) or {}
    result = get_git_ops_service().commit(data.get("workspace_id"), str(data.get("message") or ""))
    return _action_response(result)


@ops_bp.route("/git/push", methods=["POST"])
@admin_required
def git_push():
    data = request.get_json(silent=True) or {}
    result = get_git_ops_service().push(data.get("workspace_id"))
    return _action_response(result)


@ops_bp.route("/docker/status", methods=["GET"])
@check_auth
def docker_status():
    result = get_docker_engine_service().status()
    status = "error" if result.error else "success"
    return api_response(data=result.to_dict(), status=status, message=result.error.code if result.error else None, code=_status_for_error(result.error))


@ops_bp.route("/docker/containers", methods=["GET"])
@check_auth
def docker_containers():
    items = [item.to_dict() for item in get_docker_engine_service().containers()]
    return api_response(data={"items": items, "count": len(items)})


@ops_bp.route("/docker/containers/<container_id>/logs", methods=["GET"])
@check_auth
def docker_container_logs(container_id: str):
    result = get_docker_engine_service().logs(container_id, tail=int(request.args.get("tail") or 200))
    return api_response(data=result, status="success" if result.get("ok") else "error", code=200 if result.get("ok") else 400)


@ops_bp.route("/docker/containers/<container_id>/action", methods=["POST"])
@admin_required
def docker_container_action(container_id: str):
    data = request.get_json(silent=True) or {}
    result = get_docker_engine_service().action(container_id, str(data.get("action") or ""))
    return _action_response(result)


@ops_bp.route("/compose/projects", methods=["GET"])
@check_auth
def compose_projects():
    items = [item.to_dict() for item in get_docker_compose_service().projects()]
    return api_response(data={"items": items, "count": len(items)})


@ops_bp.route("/compose/projects/<project_id>/status", methods=["GET"])
@check_auth
def compose_project_status(project_id: str):
    result = get_docker_compose_service().status(project_id)
    status = "error" if result.error else "success"
    return api_response(data=result.to_dict(), status=status, message=result.error.code if result.error else None, code=_status_for_error(result.error))


@ops_bp.route("/compose/projects/<project_id>/config", methods=["GET"])
@check_auth
def compose_project_config(project_id: str):
    result = get_docker_compose_service().config(project_id)
    return api_response(data=result, status="success" if result.get("ok") else "error", code=200 if result.get("ok") else 400)


@ops_bp.route("/compose/projects/<project_id>/logs", methods=["GET"])
@check_auth
def compose_project_logs(project_id: str):
    result = get_docker_compose_service().logs(
        project_id,
        service=request.args.get("service"),
        tail=int(request.args.get("tail") or 200),
    )
    return api_response(data=result, status="success" if result.get("ok") else "error", code=200 if result.get("ok") else 400)


@ops_bp.route("/compose/projects/<project_id>/action", methods=["POST"])
@admin_required
def compose_project_action(project_id: str):
    data = request.get_json(silent=True) or {}
    result = get_docker_compose_service().action(project_id, str(data.get("action") or ""))
    return _action_response(result)
