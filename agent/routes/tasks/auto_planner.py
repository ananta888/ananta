"""Auto planner HTTP surface (CLN-048): thin handlers over the engine."""

from __future__ import annotations

import logging

from flask import Blueprint, request

from agent.auth import admin_required, check_auth
from agent.common.errors import api_response
from agent.models import (
    AutoPlannerAnalyzeRequest,
    AutoPlannerConfigureRequest,
    AutoPlannerPlanRequest,
)
from agent.services.autopilot.auto_planner_engine import (
    AutoPlanner,
    _build_followup_prompt,
    _build_planning_prompt,
    _parse_followup_analysis,
    _parse_subtasks_from_llm_response,
    auto_planner,
)
from agent.services.service_registry import get_core_services

log = logging.getLogger(__name__)

auto_planner_bp = Blueprint("tasks_auto_planner", __name__)


def init_auto_planner():
    try:
        auto_planner.restore()
    except Exception as e:
        log.warning("Auto planner restore failed: %s", e)


@auto_planner_bp.route("/tasks/auto-planner/status", methods=["GET"])
@check_auth
def auto_planner_status():
    return api_response(data=get_core_services().auto_planner_runtime_service.status(auto_planner))


@auto_planner_bp.route("/tasks/auto-planner/configure", methods=["POST"])
@check_auth
@admin_required
def auto_planner_configure():
    data = request.get_json(silent=True) or {}
    try:
        payload = AutoPlannerConfigureRequest.model_validate(data)
    except Exception:
        return api_response(status="error", message="invalid_payload", code=400)
    new_config = get_core_services().auto_planner_runtime_service.configure(
        planner=auto_planner,
        data=payload.model_dump(exclude_none=True),
    )
    return api_response(data=new_config)


@auto_planner_bp.route("/tasks/auto-planner/plan", methods=["POST"])
@check_auth
def plan_goal_endpoint():
    """
    Analysiert ein Goal und erstellt automatisch Subtasks.

    Request Body:
    {
        "goal": "Beschreibung des Ziels",
        "context": "Optionaler Kontext",
        "team_id": "Optional: Team-ID",
        "parent_task_id": "Optional: Parent-Task-ID",
        "create_tasks": true
    }
    """
    data = request.get_json(silent=True) or {}
    try:
        payload = AutoPlannerPlanRequest.model_validate(data)
    except Exception:
        return api_response(status="error", message="goal_required", code=400)
    result = get_core_services().auto_planner_runtime_service.plan_goal(
        planner=auto_planner,
        data=payload.model_dump(exclude_none=True),
    )
    if result.get("error"):
        return api_response(status="error", message=result["error"], code=result.get("code", 400))
    return api_response(data=result["data"], code=result.get("code", 201))


@auto_planner_bp.route("/tasks/auto-planner/analyze/<task_id>", methods=["POST"])
@check_auth
def analyze_task_for_followups(task_id):
    """
    Analysiert einen abgeschlossenen Task auf Folgeaufgaben.

    Request Body (optional):
    {
        "output": "Ueberschreibt die Task-Ausgabe",
        "exit_code": 0
    }
    """
    data = request.get_json(silent=True) or {}
    try:
        payload = AutoPlannerAnalyzeRequest.model_validate(data)
    except Exception:
        return api_response(status="error", message="invalid_payload", code=400)
    result = get_core_services().auto_planner_runtime_service.analyze_task_for_followups(
        planner=auto_planner,
        task_id=task_id,
        data=payload.model_dump(exclude_none=True),
    )
    if result.get("error"):
        return api_response(status="error", message=result["error"], code=result.get("code", 400))
    return api_response(data=result["data"])


__all__ = [
    "auto_planner",
    "auto_planner_bp",
    "init_auto_planner",
    "AutoPlanner",
    "_build_followup_prompt",
    "_build_planning_prompt",
    "_parse_followup_analysis",
    "_parse_subtasks_from_llm_response",
]
