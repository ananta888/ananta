import time
from typing import Any

from flask import Blueprint, current_app, g, request

from agent.auth import check_auth
from agent.common.audit import log_audit
from agent.common.errors import api_response
from agent.config import settings
from agent.db_models import GoalDB
from agent.models import GoalCreateRequest, GoalPlanNodePatchRequest, GoalProvisionRequest
from agent.services.goal_execution_contract_service import get_goal_execution_contract_service
from agent.services.product_event_service import record_product_event
from agent.services.instruction_layer_service import get_instruction_layer_service
from agent.services.repository_registry import get_repository_registry
from agent.services.service_registry import get_core_services
from agent.utils import validate_request

goals_bp = Blueprint("tasks_goals", __name__)
_SOFTWARE_GOAL_HINTS = (
    "software",
    "projekt",
    "project",
    "backend",
    "frontend",
    "api",
    "service",
    "app",
)


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


def _looks_like_software_goal(text: str) -> bool:
    normalized = str(text or "").strip().lower()
    if not normalized:
        return False
    return any(token in normalized for token in _SOFTWARE_GOAL_HINTS)


def _maybe_recover_stalled_planning_goal(goal: GoalDB) -> GoalDB:
    status = str(getattr(goal, "status", "") or "").strip().lower()
    if status != "planning":
        return goal
    goal_id = str(getattr(goal, "id", "") or "").strip()
    if not goal_id:
        return goal
    now_ts = time.time()
    updated_at = float(getattr(goal, "updated_at", 0.0) or 0.0)
    if updated_at and (now_ts - updated_at) < 30:
        return goal

    tasks = [t for t in _repos().task_repo.get_all() if str(getattr(t, "goal_id", "") or "").strip() == goal_id]
    if tasks:
        return goal

    execution_preferences = dict(getattr(goal, "execution_preferences", None) or {})
    recovery = dict(execution_preferences.get("planning_recovery") or {})
    attempts = int(recovery.get("attempts") or 0)
    last_attempt_at = float(recovery.get("last_attempt_at") or 0.0)
    if attempts >= 2:
        return goal
    if last_attempt_at and (now_ts - last_attempt_at) < 60:
        return goal

    recovery.update({"attempts": attempts + 1, "last_attempt_at": now_ts, "last_reason": "stalled_planning_no_tasks"})
    execution_preferences["planning_recovery"] = recovery
    goal.execution_preferences = execution_preferences
    goal = _repos().goal_repo.save(goal)

    try:
        effective = dict(getattr(goal, "workflow_effective", None) or {})
        from agent.routes.tasks.auto_planner import auto_planner
        result = _services().planning_service.plan_goal(
            planner=auto_planner,
            goal=str(getattr(goal, "goal", "") or ""),
            context=str(getattr(goal, "context", "") or "") or None,
            team_id=effective.get("routing", {}).get("team_id"),
            parent_task_id=None,
            create_tasks=bool(effective.get("planning", {}).get("create_tasks", True)),
            use_template=bool(effective.get("planning", {}).get("use_template", True)),
            use_repo_context=bool(effective.get("planning", {}).get("use_repo_context", True)),
            goal_id=goal.id,
            goal_trace_id=str(getattr(goal, "trace_id", "") or ""),
            mode=str(getattr(goal, "mode", "") or "generic"),
            mode_data=dict(getattr(goal, "mode_data", None) or {}),
        )
        if result.get("error"):
            recovery.update({"last_error": str(result.get("error"))[:240]})
            execution_preferences["planning_recovery"] = recovery
            goal.execution_preferences = execution_preferences
            goal = _repos().goal_repo.save(goal)
            return _services().goal_lifecycle_service.transition_goal(
                goal,
                target_status="failed",
                reason=str(result.get("error") or "planning_failed"),
                readiness=dict(getattr(goal, "readiness", None) or {}),
            )
        created_task_ids = list(result.get("created_task_ids") or [])
        if not created_task_ids:
            recovery.update({"last_error": "planning_recovery_no_tasks_created"})
            execution_preferences["planning_recovery"] = recovery
            goal.execution_preferences = execution_preferences
            goal = _repos().goal_repo.save(goal)
            return _services().goal_lifecycle_service.transition_goal(
                goal,
                target_status="failed",
                reason="planning_recovery_no_tasks_created",
                readiness=dict(getattr(goal, "readiness", None) or {}),
            )
        goal = _services().goal_lifecycle_service.transition_goal(
            goal,
            target_status="planned",
            reason="planning_recovery_completed",
            readiness=dict(getattr(goal, "readiness", None) or {}),
        )
        try:
            _services().autopilot_runtime_service.start(
                goal=goal.id,
                team_id=effective.get("routing", {}).get("team_id"),
                interval_seconds=1,
                max_concurrency=1,
                security_level="balanced",
            )
            from agent.routes.tasks.autopilot import autonomous_loop
            autonomous_loop.wake()
        except Exception:
            pass
        return goal
    except Exception as exc:
        recovery.update({"last_error": str(exc)[:240]})
        execution_preferences["planning_recovery"] = recovery
        goal.execution_preferences = execution_preferences
        return _repos().goal_repo.save(goal)


@goals_bp.route("/goals/readiness", methods=["GET"])
@check_auth
def goals_readiness():
    return api_response(data=_goal_service().goal_readiness())


@goals_bp.route("/goals/modes", methods=["GET"])
@check_auth
def list_goal_modes():
    return api_response(data=_goal_service().get_guided_modes())


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
    goal = _maybe_recover_stalled_planning_goal(goal)
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
    mode_id = str(payload.mode or "generic")
    mode_data = _goal_service().normalize_mode_data(mode_id, payload.mode_data or {})
    goal_text = str(payload.goal or "").strip()

    if mode_id != "generic":
        goal_text = _goal_service().build_goal_from_mode(mode_id, mode_data)

    if not goal_text:
        record_product_event(
            "goal_blocked",
            actor=_current_username(),
            details={"reason": "goal_required", "source": str(payload.source or "ui"), "mode": str(payload.mode or "generic")},
        )
        return api_response(status="error", message="goal_required", code=400)

    defaults = _goal_service().default_workflow_config()
    overrides = _goal_service().build_goal_workflow_overrides(payload, mode=mode_id, mode_data=mode_data)
    effective = _goal_service().deep_merge(defaults, overrides)
    provenance = _goal_service().build_provenance(defaults, overrides)
    mode_context = _goal_service().build_mode_context(mode_id, mode_data, payload.context)
    mode_constraints = _goal_service().build_mode_constraints(mode_id, mode_data)
    mode_acceptance = _goal_service().build_mode_acceptance_criteria(mode_id)
    readiness = _goal_service().goal_readiness()
    precondition_error = _goal_service().enforce_goal_preconditions(
        payload=payload,
        effective_workflow=effective,
        readiness=readiness,
        is_admin=_is_admin_request(),
    )
    if precondition_error:
        status_code = 403 if precondition_error == "policy_override_requires_admin" else 412
        record_product_event(
            "goal_blocked",
            actor=_current_username(),
            details={
                "reason": precondition_error,
                "source": str(payload.source or "ui"),
                "mode": str(payload.mode or "generic"),
                "status_code": status_code,
            },
        )
        return api_response(status="error", message=precondition_error, code=status_code)

    owner_username = str(payload.instruction_owner_username or _current_username()).strip() or _current_username()
    profile_id = str(payload.instruction_profile_id or "").strip() or None
    overlay_id = str(payload.instruction_overlay_id or "").strip() or None
    if not _is_admin_request() and owner_username != _current_username():
        return api_response(status="error", message="forbidden_instruction_owner_scope", code=403)
    if profile_id:
        profile = _repos().user_instruction_profile_repo.get_by_id(profile_id)
        if profile is None:
            return api_response(status="error", message="instruction_profile_not_found", code=404)
        if str(profile.owner_username or "").strip() != owner_username:
            return api_response(status="error", message="instruction_profile_owner_mismatch", code=409)
        profile_validation = get_instruction_layer_service().validate_user_layer_payload(
            prompt_content=str(profile.prompt_content or ""),
            metadata=dict(profile.profile_metadata or {}),
        )
        if not profile_validation.get("ok"):
            return api_response(
                status="error",
                message="instruction_policy_conflict",
                data={"source": "profile", **profile_validation},
                code=409,
            )
    if overlay_id:
        overlay = _repos().instruction_overlay_repo.get_by_id(overlay_id)
        if overlay is None:
            return api_response(status="error", message="instruction_overlay_not_found", code=404)
        if str(overlay.owner_username or "").strip() != owner_username:
            return api_response(status="error", message="instruction_overlay_owner_mismatch", code=409)
        overlay_validation = get_instruction_layer_service().validate_user_layer_payload(
            prompt_content=str(overlay.prompt_content or ""),
            metadata=dict(overlay.overlay_metadata or {}),
        )
        if not overlay_validation.get("ok"):
            return api_response(
                status="error",
                message="instruction_policy_conflict",
                data={"source": "overlay", **overlay_validation},
                code=409,
            )

    execution_preferences = dict(payload.execution_preferences or {})
    execution_preferences = get_goal_execution_contract_service().attach_to_execution_preferences(
        goal_text=goal_text,
        execution_preferences=execution_preferences,
        mode_data=mode_data,
    )
    if profile_id or overlay_id:
        execution_preferences["instruction_context"] = {
            "owner_username": owner_username,
            "profile_id": profile_id,
            "overlay_id": overlay_id,
            "updated_at": time.time(),
        }

    goal_record = GoalDB(
        goal=goal_text,
        summary=goal_text[:200],
        status="planning",
        source=str(payload.source or "ui"),
        requested_by=_current_username(),
        team_id=payload.team_id,
        context=mode_context,
        constraints=[*mode_constraints, *list(payload.constraints or [])],
        acceptance_criteria=[*mode_acceptance, *list(payload.acceptance_criteria or [])],
        execution_preferences=execution_preferences,
        visibility=dict(payload.visibility or {}),
        workflow_defaults=defaults,
        workflow_overrides=overrides,
        workflow_effective=effective,
        workflow_provenance=provenance,
        readiness=readiness,
        mode=mode_id,
        mode_data=dict(mode_data or {}),
    )
    goal_record = _repos().goal_repo.save(goal_record)
    if profile_id or overlay_id:
        goal_record.execution_preferences = dict(goal_record.execution_preferences or {})
        goal_record.execution_preferences["instruction_layers"] = get_instruction_layer_service().goal_selection_summary(goal_record)
        goal_record = _repos().goal_repo.save(goal_record)
    reference_summary = _goal_service().build_goal_reference_summary(goal_record)
    if reference_summary:
        log_audit(
            "reference_profile_used",
            {
                "goal_id": goal_record.id,
                "trace_id": goal_record.trace_id,
                "source": goal_record.source,
                "reference_profile": reference_summary,
            },
        )
        record_product_event(
            "reference_profile_selected",
            actor=_current_username(),
            details={
                "mode": goal_record.mode,
                "profile_id": reference_summary.get("profile_id"),
                "fit_level": reference_summary.get("fit_level"),
            },
            goal_id=goal_record.id,
            trace_id=goal_record.trace_id,
        )
    record_product_event(
        "product_flow_started",
        actor=_current_username(),
        details={"flow": "goal_planning", "source": goal_record.source, "mode": goal_record.mode},
        goal_id=goal_record.id,
        trace_id=goal_record.trace_id,
    )
    record_product_event(
        "goal_created",
        actor=_current_username(),
        details={"source": goal_record.source, "mode": goal_record.mode, "team_id": goal_record.team_id},
        goal_id=goal_record.id,
        trace_id=goal_record.trace_id,
    )
    goal_record = _services().goal_lifecycle_service.transition_goal(
        goal_record,
        target_status="planning",
        reason="goal_accepted_for_planning",
        readiness=readiness,
    )

    from agent.routes.tasks.auto_planner import auto_planner

    result = auto_planner.plan_goal(
        goal=goal_text,
        context=mode_context,
        team_id=effective.get("routing", {}).get("team_id"),
        create_tasks=bool(effective.get("planning", {}).get("create_tasks", True)),
        use_template=bool(effective.get("planning", {}).get("use_template", True)),
        use_repo_context=bool(effective.get("planning", {}).get("use_repo_context", True)),
        goal_id=goal_record.id,
        goal_trace_id=goal_record.trace_id,
        mode=goal_record.mode,
        mode_data=goal_record.mode_data,
    )

    current_app.logger.debug(f"plan result: {result}")

    if result.get("error"):
        goal_record = _services().goal_lifecycle_service.transition_goal(
            goal_record,
            target_status="failed",
            reason=str(result.get("error") or "planning_failed"),
            readiness=readiness,
        )
        record_product_event(
            "goal_planning_failed",
            actor="auto_planner",
            details={"reason": str(result.get("error") or "planning_failed"), "source": goal_record.source, "mode": goal_record.mode},
            goal_id=goal_record.id,
            trace_id=goal_record.trace_id,
            plan_id=result.get("plan_id"),
        )
        return api_response(status="error", message=result["error"], code=400)

    created_task_ids = list(result.get("created_task_ids") or [])
    create_tasks_enabled = bool(effective.get("planning", {}).get("create_tasks", True))
    if (
        create_tasks_enabled
        and not created_task_ids
        and mode_id == "generic"
        and _looks_like_software_goal(goal_text)
    ):
        retry_mode = "new_software_project"
        retry_mode_data = dict(goal_record.mode_data or {})
        retry_mode_data.setdefault("project_idea", goal_text)
        retry_result = auto_planner.plan_goal(
            goal=goal_text,
            context=mode_context,
            team_id=effective.get("routing", {}).get("team_id"),
            create_tasks=True,
            use_template=bool(effective.get("planning", {}).get("use_template", True)),
            use_repo_context=bool(effective.get("planning", {}).get("use_repo_context", True)),
            goal_id=goal_record.id,
            goal_trace_id=goal_record.trace_id,
            mode=retry_mode,
            mode_data=retry_mode_data,
        )
        if not retry_result.get("error"):
            result = retry_result
            created_task_ids = list(result.get("created_task_ids") or [])

    if create_tasks_enabled and not created_task_ids:
        goal_record = _services().goal_lifecycle_service.transition_goal(
            goal_record,
            target_status="failed",
            reason="planning_no_tasks_created",
            readiness=readiness,
        )
        record_product_event(
            "goal_planning_failed",
            actor="auto_planner",
            details={"reason": "planning_no_tasks_created", "source": goal_record.source, "mode": goal_record.mode},
            goal_id=goal_record.id,
            trace_id=goal_record.trace_id,
            plan_id=result.get("plan_id"),
        )
        return api_response(status="error", message="planning_no_tasks_created", code=400)

    goal_record = _services().goal_lifecycle_service.transition_goal(
        goal_record,
        target_status="planned",
        reason="planning_completed",
        readiness=readiness,
    )

    try:
        _services().autopilot_runtime_service.start(
            goal=goal_record.id,
            team_id=effective.get("routing", {}).get("team_id"),
            interval_seconds=1,
            max_concurrency=1,
            security_level="balanced",
        )
        from agent.routes.tasks.autopilot import autonomous_loop
        autonomous_loop.wake()
    except Exception:
        pass

    log_audit(
        "goal_created",
        {
            "goal_id": goal_record.id,
            "trace_id": goal_record.trace_id,
            "source": goal_record.source,
            "task_count": len(created_task_ids),
            "workflow_overrides": overrides,
            "readiness_happy_path": readiness["happy_path_ready"],
        },
    )
    record_product_event(
        "goal_planning_succeeded",
        actor="auto_planner",
        details={
            "source": goal_record.source,
            "mode": goal_record.mode,
            "created_task_count": len(created_task_ids),
            "has_plan": bool(result.get("plan_id")),
        },
        goal_id=goal_record.id,
        trace_id=goal_record.trace_id,
        plan_id=result.get("plan_id"),
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
