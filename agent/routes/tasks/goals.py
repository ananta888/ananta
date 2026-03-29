from typing import Any

from flask import Blueprint, current_app, g, request

from agent.auth import check_auth
from agent.common.audit import log_audit
from agent.common.errors import api_response
from agent.config import settings
from agent.db_models import GoalDB
from agent.models import GoalCreateRequest, GoalPlanNodePatchRequest, GoalProvisionRequest
from agent.services.repository_registry import get_repository_registry
from agent.services.service_registry import get_core_services
from agent.utils import validate_request

goals_bp = Blueprint("tasks_goals", __name__)


def _services():
    return get_core_services()


def _repos():
    return get_repository_registry()


def _goal_service():
    return _services().goal_service


def _current_username() -> str:
    user = getattr(g, "user", {}) or {}
    return str(user.get("sub") or user.get("username") or "anonymous")


def _is_admin_request() -> bool:
    return bool(getattr(g, "is_admin", False))


def _team_scope_allows(goal: GoalDB, user_payload: dict[str, Any] | None) -> bool:
    return _goal_service().team_scope_allows(goal, user_payload, _is_admin_request())


def _can_access_goal(goal: GoalDB | None) -> bool:
    return _goal_service().can_access_goal(goal, getattr(g, "user", {}) or {}, _is_admin_request())


@goals_bp.route("/goals/readiness", methods=["GET"])
@check_auth
def goals_readiness():
    return api_response(data=_goal_service().goal_readiness())


@goals_bp.route("/goals", methods=["GET"])
@check_auth
def list_goals():
    return api_response(data=[_goal_service().serialize_goal(goal) for goal in _repos().goal_repo.get_all() if _can_access_goal(goal)])


@goals_bp.route("/goals/<goal_id>", methods=["GET"])
@check_auth
def get_goal(goal_id: str):
    goal = _repos().goal_repo.get_by_id(goal_id)
    if not goal or not _can_access_goal(goal):
        return api_response(status="error", message="not_found", code=404)
    return api_response(data=_goal_service().serialize_goal(goal))


@goals_bp.route("/goals/<goal_id>/detail", methods=["GET"])
@check_auth
def get_goal_detail(goal_id: str):
    goal = _repos().goal_repo.get_by_id(goal_id)
    if not goal or not _can_access_goal(goal):
        return api_response(status="error", message="not_found", code=404)
    return api_response(data=_goal_service().goal_detail(goal, is_admin=_is_admin_request()))


@goals_bp.route("/goals/<goal_id>/plan", methods=["GET"])
@check_auth
def get_goal_plan(goal_id: str):
    goal = _repos().goal_repo.get_by_id(goal_id)
    if not goal or not _can_access_goal(goal):
        return api_response(status="error", message="not_found", code=404)
    plan, nodes = _services().planning_service.get_latest_plan_for_goal(goal_id)
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
@validate_request(GoalPlanNodePatchRequest)
def patch_goal_plan_node(goal_id: str, node_id: str):
    goal = _repos().goal_repo.get_by_id(goal_id)
    if not goal or not _can_access_goal(goal):
        return api_response(status="error", message="not_found", code=404)
    if not _is_admin_request():
        return api_response(status="error", message="forbidden", code=403)
    payload = g.validated_data.model_dump(exclude_unset=True)
    data, error = _services().planning_service.patch_plan_node(goal_id, node_id, payload)
    if error:
        code = 404 if error in {"plan_not_found", "node_not_found"} else 400
        return api_response(status="error", message=error, code=code)
    plan, _ = _services().planning_service.get_latest_plan_for_goal(goal_id)
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
    goal = _repos().goal_repo.get_by_id(goal_id)
    if not goal or not _can_access_goal(goal):
        return api_response(status="error", message="not_found", code=404)
    summary = _services().verification_service.governance_summary(goal_id, include_sensitive=_is_admin_request())
    if not summary:
        return api_response(status="error", message="not_found", code=404)
    return api_response(data=_goal_service().sanitize_governance_summary(summary, _is_admin_request()))


@goals_bp.route("/goals/test/provision", methods=["POST"])
@check_auth
@validate_request(GoalProvisionRequest)
def test_provision_goal():
    if not settings.auth_test_endpoints_enabled:
        return api_response(status="error", message="not_found", code=404)
    if not _is_admin_request():
        return api_response(status="error", message="forbidden", code=403)

    data: GoalProvisionRequest = g.validated_data
    goal_text = str(data.goal or "").strip()
    if not goal_text:
        return api_response(status="error", message="goal_required", code=400)

    summary = str(data.summary or goal_text[:200]).strip() or goal_text[:200]
    status = str(data.status or "planned").strip() or "planned"
    goal = _repos().goal_repo.save(
        GoalDB(
            goal=goal_text,
            summary=summary,
            status=status,
            source=str(data.source or "test"),
            requested_by=_current_username(),
            team_id=data.team_id,
            context=data.context,
            constraints=list(data.constraints or []),
            acceptance_criteria=list(data.acceptance_criteria or []),
            execution_preferences=dict(data.execution_preferences or {}),
            visibility=dict(data.visibility or {}),
            workflow_defaults=_goal_service().default_workflow_config(),
            workflow_overrides={},
            workflow_effective=_goal_service().default_workflow_config(),
            workflow_provenance={},
            readiness=_goal_service().goal_readiness(),
        )
    )
    return api_response(data=_goal_service().serialize_goal(goal))


@goals_bp.route("/goals", methods=["POST"])
@check_auth
@validate_request(GoalCreateRequest)
def create_goal():
    payload: GoalCreateRequest = g.validated_data
    goal_text = str(payload.goal or "").strip()
    if not goal_text:
        return api_response(status="error", message="goal_required", code=400)

    defaults = _goal_service().default_workflow_config()
    overrides = _goal_service().build_goal_workflow_overrides(payload)
    effective = _goal_service().deep_merge(defaults, overrides)
    provenance = _goal_service().build_provenance(defaults, overrides)
    readiness = _goal_service().goal_readiness()
    precondition_error = _goal_service().enforce_goal_preconditions(
        payload=payload,
        effective_workflow=effective,
        readiness=readiness,
        is_admin=_is_admin_request(),
    )
    if precondition_error:
        status_code = 403 if precondition_error == "policy_override_requires_admin" else 412
        return api_response(status="error", message=precondition_error, code=status_code)

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
    goal_record = _repos().goal_repo.save(goal_record)
    goal_record = _services().goal_lifecycle_service.transition_goal(
        goal_record,
        target_status="planning",
        reason="goal_accepted_for_planning",
        readiness=readiness,
    )

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

    if result.get("error"):
        goal_record = _services().goal_lifecycle_service.transition_goal(
            goal_record,
            target_status="failed",
            reason=str(result.get("error") or "planning_failed"),
            readiness=readiness,
        )
        return api_response(status="error", message=result["error"], code=400)

    goal_record = _services().goal_lifecycle_service.transition_goal(
        goal_record,
        target_status="planned",
        reason="planning_completed",
        readiness=readiness,
    )

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
            "goal": _goal_service().serialize_goal(goal_record),
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
