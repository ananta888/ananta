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
    if error.code in {
        "workspace_not_allowed",
        "path_not_allowed",
        "git_remote_not_allowed",
        "git_branch_not_allowed",
        "git_untracked_discard_denied",
        "compose_project_not_registered",
        "docker_container_not_registered",
    }:
        return 403
    if error.code in {
        "approval_required",
        "git_detached_head",
        "git_dirty_worktree",
        "git_conflict",
        "git_operation_in_progress",
        "git_no_upstream",
        "git_path_state_invalid",
        "git_nothing_to_commit",
    }:
        return 409
    if error.code in {"policy_denied", "docker_boundary_not_configured", "docker_permission_denied"}:
        return 403
    if error.code in {"git_not_found", "docker_not_found", "docker_unreachable", "compose_plugin_missing"}:
        return 503
    return 400


def _action_response(result: OpsActionResult):
    status = "success" if result.ok else "error"
    return api_response(
        data=result.to_dict(),
        status=status,
        message=result.error.code if result.error else None,
        code=_status_for_error(result.error),
    )


def _ops_dict_response(result: dict):
    error_payload = result.get("error") if isinstance(result.get("error"), dict) else None
    error = None
    if error_payload:
        error = OpsError(
            str(error_payload.get("code") or "docker_unreachable"),
            str(error_payload.get("message") or "Ops request failed"),
            dict(error_payload.get("details") or {}),
        )
    return api_response(
        data=result,
        status="success" if result.get("ok") else "error",
        message=error.code if error else None,
        code=200 if result.get("ok") else _status_for_error(error),
    )


def _boolean_arg(name: str) -> bool:
    return str(request.args.get(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def _bounded_int_arg(name: str, *, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(request.args.get(name) or default)
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


def _git_dto_response(result):
    error = getattr(result, "error", None)
    return api_response(
        data=result.to_dict(),
        status="error" if error else "success",
        message=error.code if error else None,
        code=_status_for_error(error),
    )


@ops_bp.route("/git/workspaces", methods=["GET"])
@check_auth
def git_workspaces():
    items = get_git_ops_service().workspaces()
    return api_response(data={"items": items, "count": len(items)})


@ops_bp.route("/git/status", methods=["GET"])
@check_auth
def git_status():
    return _git_dto_response(get_git_ops_service().status(request.args.get("workspace_id")))


@ops_bp.route("/git/changes", methods=["GET"])
@check_auth
def git_changes():
    return _git_dto_response(get_git_ops_service().changes(request.args.get("workspace_id")))


@ops_bp.route("/git/history", methods=["GET"])
@check_auth
def git_history():
    result = get_git_ops_service().history(
        request.args.get("workspace_id"),
        limit=_bounded_int_arg("limit", default=50, minimum=1, maximum=200),
        offset=_bounded_int_arg("offset", default=0, minimum=0, maximum=100_000),
        path=request.args.get("path"),
    )
    return _git_dto_response(result)


@ops_bp.route("/git/branches", methods=["GET"])
@check_auth
def git_branches():
    return _git_dto_response(get_git_ops_service().branches(request.args.get("workspace_id")))


@ops_bp.route("/git/remotes", methods=["GET"])
@check_auth
def git_remotes():
    return _git_dto_response(get_git_ops_service().remotes(request.args.get("workspace_id")))


@ops_bp.route("/git/activity", methods=["GET"])
@check_auth
def git_activity():
    result = get_git_ops_service().activity(
        request.args.get("workspace_id"),
        limit=_bounded_int_arg("limit", default=100, minimum=1, maximum=300),
    )
    return _git_dto_response(result)


@ops_bp.route("/git/diff", methods=["GET"])
@check_auth
def git_diff():
    result = get_git_ops_service().diff(
        request.args.get("workspace_id"),
        path=request.args.get("path"),
        cached=_boolean_arg("cached"),
        scope=request.args.get("scope"),
    )
    return _git_dto_response(result)


@ops_bp.route("/git/stage", methods=["POST"])
@admin_required
def git_stage():
    data = request.get_json(silent=True) or {}
    result = get_git_ops_service().stage(
        data.get("workspace_id"),
        data.get("paths") or [],
        staged=not bool(data.get("unstage")),
        approval_id=str(data.get("approval_id") or "") or None,
    )
    return _action_response(result)


@ops_bp.route("/git/unstage", methods=["POST"])
@admin_required
def git_unstage():
    data = request.get_json(silent=True) or {}
    result = get_git_ops_service().unstage(
        data.get("workspace_id"),
        data.get("paths") or [],
        approval_id=str(data.get("approval_id") or "") or None,
    )
    return _action_response(result)


@ops_bp.route("/git/discard", methods=["POST"])
@admin_required
def git_discard():
    data = request.get_json(silent=True) or {}
    result = get_git_ops_service().discard(
        data.get("workspace_id"),
        data.get("paths") or [],
        approval_id=str(data.get("approval_id") or "") or None,
    )
    return _action_response(result)


@ops_bp.route("/git/commit", methods=["POST"])
@admin_required
def git_commit():
    data = request.get_json(silent=True) or {}
    result = get_git_ops_service().commit(
        data.get("workspace_id"),
        str(data.get("message") or ""),
        approval_id=str(data.get("approval_id") or "") or None,
    )
    return _action_response(result)


@ops_bp.route("/git/fetch", methods=["POST"])
@admin_required
def git_fetch():
    data = request.get_json(silent=True) or {}
    result = get_git_ops_service().fetch(
        data.get("workspace_id"),
        remote=str(data.get("remote") or "") or None,
        approval_id=str(data.get("approval_id") or "") or None,
    )
    return _action_response(result)


@ops_bp.route("/git/pull", methods=["POST"])
@admin_required
def git_pull():
    data = request.get_json(silent=True) or {}
    result = get_git_ops_service().pull(
        data.get("workspace_id"),
        remote=str(data.get("remote") or "") or None,
        branch=str(data.get("branch") or "") or None,
        approval_id=str(data.get("approval_id") or "") or None,
    )
    return _action_response(result)


@ops_bp.route("/git/push", methods=["POST"])
@admin_required
def git_push():
    data = request.get_json(silent=True) or {}
    result = get_git_ops_service().push(
        data.get("workspace_id"),
        remote=str(data.get("remote") or "") or None,
        branch=str(data.get("branch") or "") or None,
        approval_id=str(data.get("approval_id") or "") or None,
    )
    return _action_response(result)


@ops_bp.route("/docker/status", methods=["GET"])
@check_auth
def docker_status():
    result = get_docker_engine_service().status()
    status = "error" if result.error else "success"
    return api_response(
        data=result.to_dict(),
        status=status,
        message=result.error.code if result.error else None,
        code=_status_for_error(result.error),
    )


@ops_bp.route("/docker/info", methods=["GET"])
@check_auth
def docker_info():
    return _ops_dict_response(get_docker_engine_service().info())


@ops_bp.route("/docker/containers", methods=["GET"])
@check_auth
def docker_containers():
    return _ops_dict_response(get_docker_engine_service().container_snapshot())


@ops_bp.route("/docker/images", methods=["GET"])
@check_auth
def docker_images():
    return _ops_dict_response(get_docker_engine_service().images())


@ops_bp.route("/docker/networks", methods=["GET"])
@check_auth
def docker_networks():
    return _ops_dict_response(get_docker_engine_service().networks())


@ops_bp.route("/docker/volumes", methods=["GET"])
@check_auth
def docker_volumes():
    return _ops_dict_response(get_docker_engine_service().volumes())


@ops_bp.route("/docker/disk-usage", methods=["GET"])
@check_auth
def docker_disk_usage():
    return _ops_dict_response(get_docker_engine_service().disk_usage())


@ops_bp.route("/docker/containers/<container_id>/inspect", methods=["GET"])
@check_auth
def docker_container_inspect(container_id: str):
    return _ops_dict_response(get_docker_engine_service().inspect_light(container_id))


@ops_bp.route("/docker/containers/<container_id>/stats", methods=["GET"])
@check_auth
def docker_container_stats(container_id: str):
    return _ops_dict_response(get_docker_engine_service().stats(container_id))


@ops_bp.route("/docker/containers/<container_id>/logs", methods=["GET"])
@check_auth
def docker_container_logs(container_id: str):
    result = get_docker_engine_service().logs(
        container_id,
        tail=request.args.get("tail") or 200,
        timestamps=_boolean_arg("timestamps"),
    )
    return _ops_dict_response(result)


@ops_bp.route("/docker/containers/<container_id>/action", methods=["POST"])
@admin_required
def docker_container_action(container_id: str):
    data = request.get_json(silent=True) or {}
    result = get_docker_engine_service().action(
        container_id,
        str(data.get("action") or ""),
        approval_id=str(data.get("approval_id") or "") or None,
    )
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
    return api_response(
        data=result.to_dict(),
        status=status,
        message=result.error.code if result.error else None,
        code=_status_for_error(result.error),
    )


@ops_bp.route("/compose/projects/<project_id>/config", methods=["GET"])
@check_auth
def compose_project_config(project_id: str):
    result = get_docker_compose_service().config(project_id)
    return _ops_dict_response(result)


@ops_bp.route("/compose/projects/<project_id>/logs", methods=["GET"])
@check_auth
def compose_project_logs(project_id: str):
    result = get_docker_compose_service().logs(
        project_id,
        service=request.args.get("service"),
        tail=request.args.get("tail") or 200,
        timestamps=_boolean_arg("timestamps"),
    )
    return _ops_dict_response(result)


@ops_bp.route("/compose/projects/<project_id>/action", methods=["POST"])
@admin_required
def compose_project_action(project_id: str):
    data = request.get_json(silent=True) or {}
    result = get_docker_compose_service().action(
        project_id,
        str(data.get("action") or ""),
        service=str(data.get("service") or "") or None,
        approval_id=str(data.get("approval_id") or "") or None,
    )
    return _action_response(result)
