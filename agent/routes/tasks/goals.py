import copy
import time
from typing import Any

from flask import Blueprint, current_app, g, request

from agent.auth import check_auth
from agent.common.audit import log_audit
from agent.common.errors import api_response
from agent.config import settings
from agent.db_models import GoalDB
from agent.models import GoalCreateRequest
from agent.repository import agent_repo, goal_repo, plan_repo, task_repo, team_repo, verification_record_repo
from agent.services.planning_service import get_goal_feature_flags, get_planning_service
from agent.services.verification_service import get_verification_service
from agent.utils import validate_request

goals_bp = Blueprint("tasks_goals", __name__)


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _flatten_dict(data: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    flat: dict[str, Any] = {}
    for key, value in (data or {}).items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            flat.update(_flatten_dict(value, path))
        else:
            flat[path] = value
    return flat


def _build_provenance(defaults: dict[str, Any], overrides: dict[str, Any]) -> dict[str, str]:
    provenance = {key: "default" for key in _flatten_dict(defaults)}
    for key in _flatten_dict(overrides):
        provenance[key] = "override"
    return provenance


def _default_goal_workflow_config() -> dict[str, Any]:
    agent_cfg = current_app.config.get("AGENT_CONFIG", {}) or {}
    planning_defaults = {
        "engine": "auto_planner",
        "create_tasks": True,
        "use_template": True,
        "use_repo_context": True,
        "max_subtasks_per_goal": 8,
    }
    routing_defaults = {
        "mode": "active_team_or_hub_default",
        "team_id": None,
        "worker_selection": "current_assignment_flow",
    }
    verification_defaults = {
        "mode": "existing_quality_gates",
        "enabled": bool((agent_cfg.get("quality_gates") or {}).get("enabled", True)),
    }
    artifact_defaults = {
        "result_view": "task_summary",
        "include_task_tree": True,
    }
    policy_defaults = {
        "mode": "hub_enforced",
        "security_level": "safe_defaults",
    }
    return {
        "planning": planning_defaults,
        "routing": routing_defaults,
        "verification": verification_defaults,
        "artifacts": artifact_defaults,
        "policy": policy_defaults,
    }


def _build_goal_workflow_overrides(payload: GoalCreateRequest) -> dict[str, Any]:
    overrides = copy.deepcopy(payload.workflow or {})
    if payload.team_id:
        overrides.setdefault("routing", {})["team_id"] = payload.team_id
    if payload.create_tasks is not None:
        overrides.setdefault("planning", {})["create_tasks"] = bool(payload.create_tasks)
    if payload.use_template is not None:
        overrides.setdefault("planning", {})["use_template"] = bool(payload.use_template)
    if payload.use_repo_context is not None:
        overrides.setdefault("planning", {})["use_repo_context"] = bool(payload.use_repo_context)
    return overrides


def _goal_readiness() -> dict[str, Any]:
    agent_cfg = current_app.config.get("AGENT_CONFIG", {}) or {}
    llm_cfg = agent_cfg.get("llm_config", {}) or {}
    workers = agent_repo.get_all()
    active_team = next((team for team in team_repo.get_all() if team.is_active), None)
    local_worker_available = bool(settings.role == "worker" or getattr(settings, "hub_can_be_worker", False))
    worker_available = bool(workers) or local_worker_available
    planning_available = bool(llm_cfg.get("provider")) or True
    degraded_hints: list[str] = []

    if not workers and local_worker_available:
        degraded_hints.append("no_remote_workers_registered_using_local_worker_fallback")
    if not active_team:
        degraded_hints.append("no_active_team_default_routing_will_use_existing_assignment_flow")

    return {
        "happy_path_ready": bool(worker_available and planning_available),
        "planning_available": planning_available,
        "worker_available": worker_available,
        "active_team_id": active_team.id if active_team else None,
        "available_worker_count": len(workers),
        "degraded_hints": degraded_hints,
        "defaults": _default_goal_workflow_config(),
        "feature_flags": get_goal_feature_flags(),
    }


def _serialize_goal(goal: GoalDB) -> dict[str, Any]:
    data = goal.model_dump()
    data["task_count"] = len(task_repo.get_by_goal_id(goal.id))
    return data


def _current_username() -> str:
    user = getattr(g, "user", {}) or {}
    return str(user.get("sub") or user.get("username") or "anonymous")


def _is_admin_request() -> bool:
    return bool(getattr(g, "is_admin", False))


def _team_scope_allows(goal: GoalDB, user_payload: dict[str, Any] | None) -> bool:
    if not goal.team_id or _is_admin_request():
        return True
    user_payload = user_payload or {}
    return bool(user_payload.get("team_id")) and str(user_payload.get("team_id")) == str(goal.team_id)


def _can_access_goal(goal: GoalDB | None) -> bool:
    if not goal:
        return False
    return _team_scope_allows(goal, getattr(g, "user", {}) or {})


def _sanitize_governance_summary(summary: dict[str, Any], is_admin: bool) -> dict[str, Any]:
    if is_admin:
        return summary
    return {
        "goal_id": summary.get("goal_id"),
        "trace_id": summary.get("trace_id"),
        "policy": {
            "total": (summary.get("policy") or {}).get("total", 0),
            "approved": (summary.get("policy") or {}).get("approved", 0),
            "blocked": (summary.get("policy") or {}).get("blocked", 0),
        },
        "verification": {
            "total": (summary.get("verification") or {}).get("total", 0),
            "passed": (summary.get("verification") or {}).get("passed", 0),
            "failed": (summary.get("verification") or {}).get("failed", 0),
            "escalated": (summary.get("verification") or {}).get("escalated", 0),
        },
        "summary": {
            **dict(summary.get("summary") or {}),
            "governance_visible": False,
            "detail_level": "restricted",
        },
    }


def _build_artifact_summary(goal: GoalDB) -> dict[str, Any]:
    tasks = task_repo.get_by_goal_id(goal.id)
    verification_records = verification_record_repo.get_by_goal_id(goal.id)
    task_outputs = [
        {
            "task_id": task.id,
            "title": task.title,
            "status": task.status,
            "plan_node_id": task.plan_node_id,
            "preview": str(task.last_output or "")[:280],
            "trace_id": task.goal_trace_id,
        }
        for task in tasks
        if task.last_output
    ]
    latest_output = next((item for item in task_outputs if item.get("preview")), None)
    return {
        "goal_id": goal.id,
        "trace_id": goal.trace_id,
        "result_summary": {
            "status": goal.status,
            "task_count": len(tasks),
            "completed_tasks": len([task for task in tasks if task.status == "completed"]),
            "failed_tasks": len([task for task in tasks if task.status == "failed"]),
            "verification_passed": len([record for record in verification_records if record.status == "passed"]),
        },
        "headline_artifact": latest_output,
        "artifacts": task_outputs[:10],
    }


def _goal_detail(goal: GoalDB) -> dict[str, Any]:
    plan, nodes = get_planning_service().get_latest_plan_for_goal(goal.id)
    tasks = task_repo.get_by_goal_id(goal.id)
    governance = get_verification_service().governance_summary(goal.id)
    return {
        "goal": _serialize_goal(goal),
        "trace": {
            "trace_id": goal.trace_id,
            "goal_id": goal.id,
            "plan_id": plan.id if plan else None,
            "task_ids": [task.id for task in tasks],
        },
        "artifacts": _build_artifact_summary(goal),
        "plan": {
            "plan": plan.model_dump() if plan else None,
            "nodes": [node.model_dump() for node in nodes],
        },
        "tasks": [
            {
                "id": task.id,
                "title": task.title,
                "status": task.status,
                "priority": task.priority,
                "plan_node_id": task.plan_node_id,
                "verification_status": dict(task.verification_status or {}),
                "trace_id": task.goal_trace_id,
            }
            for task in tasks
        ],
        "governance": _sanitize_governance_summary(governance, _is_admin_request()) if governance else None,
    }


@goals_bp.route("/goals/readiness", methods=["GET"])
@check_auth
def goals_readiness():
    return api_response(data=_goal_readiness())


@goals_bp.route("/goals", methods=["GET"])
@check_auth
def list_goals():
    return api_response(data=[_serialize_goal(goal) for goal in goal_repo.get_all() if _can_access_goal(goal)])


@goals_bp.route("/goals/<goal_id>", methods=["GET"])
@check_auth
def get_goal(goal_id: str):
    goal = goal_repo.get_by_id(goal_id)
    if not goal or not _can_access_goal(goal):
        return api_response(status="error", message="not_found", code=404)
    return api_response(data=_serialize_goal(goal))


@goals_bp.route("/goals/<goal_id>/detail", methods=["GET"])
@check_auth
def get_goal_detail(goal_id: str):
    goal = goal_repo.get_by_id(goal_id)
    if not goal or not _can_access_goal(goal):
        return api_response(status="error", message="not_found", code=404)
    return api_response(data=_goal_detail(goal))


@goals_bp.route("/goals/<goal_id>/plan", methods=["GET"])
@check_auth
def get_goal_plan(goal_id: str):
    goal = goal_repo.get_by_id(goal_id)
    if not goal or not _can_access_goal(goal):
        return api_response(status="error", message="not_found", code=404)
    plan, nodes = get_planning_service().get_latest_plan_for_goal(goal_id)
    if not plan:
        return api_response(status="error", message="plan_not_found", code=404)
    return api_response(
        data={
            "goal_id": goal_id,
            "plan": plan.model_dump(),
            "nodes": [node.model_dump() for node in nodes],
        }
    )


@goals_bp.route("/goals/<goal_id>/plan/nodes/<node_id>", methods=["PATCH"])
@check_auth
def patch_goal_plan_node(goal_id: str, node_id: str):
    goal = goal_repo.get_by_id(goal_id)
    if not goal or not _can_access_goal(goal):
        return api_response(status="error", message="not_found", code=404)
    if not _is_admin_request():
        return api_response(status="error", message="forbidden", code=403)
    payload = request.get_json(silent=True) or {}
    data, error = get_planning_service().patch_plan_node(goal_id, node_id, payload)
    if error:
        code = 404 if error in {"plan_not_found", "node_not_found"} else 400
        return api_response(status="error", message=error, code=code)
    plan, _ = get_planning_service().get_latest_plan_for_goal(goal_id)
    log_audit(
        "plan_node_updated",
        {
            "goal_id": goal_id,
            "plan_id": plan.id if plan else None,
            "trace_id": goal.trace_id,
            "node_id": node_id,
            "changes": payload,
        },
    )
    return api_response(data=data)


@goals_bp.route("/goals/<goal_id>/governance-summary", methods=["GET"])
@check_auth
def goal_governance_summary(goal_id: str):
    goal = goal_repo.get_by_id(goal_id)
    if not goal or not _can_access_goal(goal):
        return api_response(status="error", message="not_found", code=404)
    summary = get_verification_service().governance_summary(goal_id)
    if not summary:
        return api_response(status="error", message="not_found", code=404)
    return api_response(data=_sanitize_governance_summary(summary, _is_admin_request()))


@goals_bp.route("/goals", methods=["POST"])
@check_auth
@validate_request(GoalCreateRequest)
def create_goal():
    payload: GoalCreateRequest = g.validated_data
    goal_text = str(payload.goal or "").strip()
    if not goal_text:
        return api_response(status="error", message="goal_required", code=400)

    defaults = _default_goal_workflow_config()
    overrides = _build_goal_workflow_overrides(payload)
    effective = _deep_merge(defaults, overrides)
    provenance = _build_provenance(defaults, overrides)
    readiness = _goal_readiness()

    goal_record = GoalDB(
        goal=goal_text,
        summary=goal_text[:200],
        status="planning",
        source=str(payload.source or "ui"),
        requested_by=_current_username(),
        team_id=payload.team_id,
        context=payload.context,
        constraints=list(payload.constraints or []),
        acceptance_criteria=list(payload.acceptance_criteria or []),
        execution_preferences=dict(payload.execution_preferences or {}),
        visibility=dict(payload.visibility or {}),
        workflow_defaults=defaults,
        workflow_overrides=overrides,
        workflow_effective=effective,
        workflow_provenance=provenance,
        readiness=readiness,
    )
    goal_record = goal_repo.save(goal_record)

    from agent.routes.tasks.auto_planner import auto_planner

    result = auto_planner.plan_goal(
        goal=goal_text,
        context=payload.context,
        team_id=effective.get("routing", {}).get("team_id"),
        create_tasks=bool(effective.get("planning", {}).get("create_tasks", True)),
        use_template=bool(effective.get("planning", {}).get("use_template", True)),
        use_repo_context=bool(effective.get("planning", {}).get("use_repo_context", True)),
        goal_id=goal_record.id,
        goal_trace_id=goal_record.trace_id,
    )

    current_app.logger.debug(f"plan result: {result}")

    goal_record.updated_at = time.time()
    if result.get("error"):
        goal_record.status = "failed"
        goal_repo.save(goal_record)
        return api_response(status="error", message=result["error"], code=400)

    goal_record.status = "planned"
    goal_repo.save(goal_record)

    log_audit(
        "goal_created",
        {
            "goal_id": goal_record.id,
            "trace_id": goal_record.trace_id,
            "source": goal_record.source,
            "task_count": len(result.get("created_task_ids") or []),
            "workflow_overrides": overrides,
            "readiness_happy_path": readiness["happy_path_ready"],
        },
    )

    return api_response(
        data={
            "goal": _serialize_goal(goal_record),
            "created_task_ids": result.get("created_task_ids", []),
            "subtasks": result.get("subtasks", []),
            "workflow": {
                "defaults": defaults,
                "overrides": overrides,
                "effective": effective,
                "provenance": provenance,
            },
            "readiness": readiness,
            "plan_id": result.get("plan_id"),
            "plan_node_ids": result.get("plan_node_ids", []),
        },
        code=201,
    )
