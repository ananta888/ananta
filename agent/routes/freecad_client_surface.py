from __future__ import annotations

from flask import Blueprint, g, request

from agent.auth import check_auth
from agent.common.audit import log_audit
from agent.common.errors import api_response
from agent.services.freecad_client_surface_service import get_freecad_client_surface_service

freecad_client_surface_bp = Blueprint("freecad_client_surface", __name__)


def _service():
    return get_freecad_client_surface_service()


def _current_username() -> str:
    user = getattr(g, "user", {}) or {}
    return str(user.get("sub") or user.get("username") or "anonymous")


@freecad_client_surface_bp.route("/health", methods=["GET"])
@check_auth
def health() -> tuple:
    return api_response(data=_service().health())


@freecad_client_surface_bp.route("/capabilities", methods=["GET"])
@check_auth
def capabilities() -> tuple:
    return api_response(data=_service().capabilities())


@freecad_client_surface_bp.route("/goals", methods=["POST"])
@check_auth
def submit_goal() -> tuple:
    payload = request.get_json(silent=True) or {}
    result = _service().submit_goal(
        goal=str(payload.get("goal") or ""),
        context=dict(payload.get("context") or {}),
        capability_id=str(payload.get("capability_id") or "freecad.model.inspect"),
        requested_by=_current_username(),
    )
    if result.get("status") != "accepted":
        return api_response(status="error", message=str(result.get("reason") or "goal_submit_failed"), data=result, code=400)
    log_audit("freecad_goal_submitted", {"goal_id": result.get("goal_id"), "trace_id": result.get("trace_id")})
    return api_response(data=result, code=201)


@freecad_client_surface_bp.route("/approvals/decision", methods=["POST"])
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
    log_audit("freecad_approval_decision", {"approval_id": result.get("approval_id"), "decision": result.get("decision")})
    return api_response(data=result, code=status_code)


@freecad_client_surface_bp.route("/export-plans", methods=["POST"])
@check_auth
def export_plan() -> tuple:
    payload = request.get_json(silent=True) or {}
    result = _service().export_plan(
        fmt=str(payload.get("format") or "STEP"),
        target_path=str(payload.get("target_path") or ""),
        selection_only=bool(payload.get("selection_only")),
    )
    return api_response(data=result)


@freecad_client_surface_bp.route("/macro-plans", methods=["POST"])
@check_auth
def macro_plan() -> tuple:
    payload = request.get_json(silent=True) or {}
    result = _service().macro_plan(
        objective=str(payload.get("objective") or ""),
        context_summary=dict(payload.get("context_summary") or {}),
    )
    if result.get("status") != "accepted":
        return api_response(status="error", message=str(result.get("reason") or "macro_plan_failed"), data=result, code=400)
    return api_response(data=result)


@freecad_client_surface_bp.route("/macro-executions", methods=["POST"])
@check_auth
def macro_execution() -> tuple:
    payload = request.get_json(silent=True) or {}
    result, status_code = _service().macro_execute(
        macro_text=str(((payload.get("payload") or {}).get("macro_text") or "")),
        approval_id=payload.get("approval_id"),
        correlation_id=str(payload.get("correlation_id") or ""),
    )
    if status_code >= 400:
        return api_response(status="error", message=str(result.get("reason") or "approval_required"), data=result, code=status_code)
    log_audit("freecad_macro_execution_requested", {"correlation_id": payload.get("correlation_id"), "approval_id": payload.get("approval_id")})
    return api_response(data=result, code=status_code)
