from __future__ import annotations

from flask import Blueprint, g, request

from agent.auth import check_auth
from agent.common.audit import log_audit
from agent.common.errors import api_response
from agent.services.blender_client_surface_service import get_blender_client_surface_service

blender_client_surface_bp = Blueprint("blender_client_surface", __name__)


def _service():
    return get_blender_client_surface_service()


def _current_username() -> str:
    user = getattr(g, "user", {}) or {}
    return str(user.get("sub") or user.get("username") or "anonymous")


@blender_client_surface_bp.route("/health", methods=["GET"])
@check_auth
def health() -> tuple:
    return api_response(data=_service().health())


@blender_client_surface_bp.route("/capabilities", methods=["GET"])
@check_auth
def capabilities() -> tuple:
    return api_response(data=_service().capabilities())


@blender_client_surface_bp.route("/goals", methods=["POST"])
@check_auth
def submit_goal() -> tuple:
    payload = request.get_json(silent=True) or {}
    result = _service().submit_goal(
        goal=str(payload.get("goal") or ""),
        context=dict(payload.get("context") or {}),
        capability_id=str(payload.get("capability_id") or "blender.scene.read"),
        requested_by=_current_username(),
    )
    if result.get("status") != "accepted":
        return api_response(status="error", message=str(result.get("reason") or "goal_submit_failed"), data=result, code=400)
    log_audit("blender_goal_submitted", {"goal_id": result.get("goal_id"), "trace_id": result.get("trace_id")})
    return api_response(data=result, code=201)


@blender_client_surface_bp.route("/tasks", methods=["GET"])
@check_auth
def list_tasks() -> tuple:
    return api_response(data=_service().list_tasks())


@blender_client_surface_bp.route("/tasks/<task_id>", methods=["GET"])
@check_auth
def get_task(task_id: str) -> tuple:
    result, status_code = _service().get_task(task_id=task_id)
    if status_code >= 400:
        return api_response(status="error", message=str(result.get("reason") or "task_not_found"), data=result, code=status_code)
    return api_response(data=result)


@blender_client_surface_bp.route("/artifacts", methods=["GET"])
@check_auth
def list_artifacts() -> tuple:
    return api_response(data=_service().list_artifacts())


@blender_client_surface_bp.route("/artifacts/<artifact_id>", methods=["GET"])
@check_auth
def get_artifact(artifact_id: str) -> tuple:
    result, status_code = _service().get_artifact(artifact_id=artifact_id)
    if status_code >= 400:
        return api_response(status="error", message=str(result.get("reason") or "artifact_not_found"), data=result, code=status_code)
    return api_response(data=result)


@blender_client_surface_bp.route("/approvals", methods=["GET"])
@check_auth
def list_approvals() -> tuple:
    return api_response(data=_service().list_approvals())


@blender_client_surface_bp.route("/approvals/decision", methods=["POST"])
@check_auth
def approval_decision() -> tuple:
    payload = request.get_json(silent=True) or {}
    result, status_code = _service().approval_decision(
        approval_id=str(payload.get("approval_id") or ""),
        decision=str(payload.get("decision") or ""),
        requested_by=_current_username(),
    )
    if status_code >= 400:
        return api_response(status="error", message=str(result.get("reason") or "approval_decision_failed"), data=result, code=status_code)
    log_audit("blender_approval_decision", {"approval_id": result.get("approval_id"), "decision": result.get("decision")})
    return api_response(data=result, code=status_code)


@blender_client_surface_bp.route("/export-plans", methods=["POST"])
@check_auth
def export_plan() -> tuple:
    payload = request.get_json(silent=True) or {}
    return api_response(
        data=_service().export_plan(
            fmt=str(payload.get("format") or "GLTF"),
            target_path=str(payload.get("target_path") or ""),
            selection_only=bool(payload.get("selection_only")),
        )
    )


@blender_client_surface_bp.route("/render-plans", methods=["POST"])
@check_auth
def render_plan() -> tuple:
    return api_response(data=_service().render_plan(payload=request.get_json(silent=True) or {}))


@blender_client_surface_bp.route("/mutation-plans", methods=["POST"])
@check_auth
def mutation_plan() -> tuple:
    return api_response(data=_service().mutation_plan(payload=request.get_json(silent=True) or {}))


@blender_client_surface_bp.route("/executions", methods=["POST"])
@check_auth
def execute() -> tuple:
    result, status_code = _service().execute(payload=request.get_json(silent=True) or {})
    if status_code >= 400:
        return api_response(status="error", message=str(result.get("reason") or "approval_required"), data=result, code=status_code)
    return api_response(data=result, code=status_code)


@blender_client_surface_bp.route("/events", methods=["GET"])
@check_auth
def events() -> tuple:
    return api_response(data=_service().events())
