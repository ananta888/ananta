import threading
import time
from typing import Any

from flask import Blueprint, current_app, g, request

from agent.auth import check_auth
from agent.common.audit import log_audit
from agent.common.errors import api_response
from agent.config import settings
from agent.db_models import GoalDB
from agent.models import GoalCreateRequest, GoalProvisionRequest
from agent.services.goal_execution_contract_service import get_goal_execution_contract_service
from agent.services.goal_config_resolver_service import ALLOWED_GOAL_CONFIG_KEYS, get_goal_config_resolver_service
from agent.services.config_profile_service import get_config_profile_service
from agent.services.planning_quality_service import get_planning_quality_service
from agent.services.planning_contract import resolve_planning_contract
from agent.services.planning_validation_service import get_planning_validation_service
from agent.services.product_event_service import record_product_event
from agent.services.instruction_layer_service import get_instruction_layer_service
from agent.services.repository_registry import get_repository_registry
from agent.services.service_registry import get_core_services
from agent.utils import validate_request

from agent.routes.tasks.goals_helpers import (
    _cancel_stale_planning_goals,
    _current_username,
    _goal_service,
    _is_admin_request,
    _repos,
    _services,
)

goals_bp = Blueprint("tasks_goals", __name__)

# Planning slot state kept here for test compatibility — tests access these
# via `import agent.routes.tasks.goals as goals_mod` and set them directly.
_PLANNING_SLOTS_LOCK = threading.Lock()
_PLANNING_SLOTS: threading.Semaphore | None = None
_PLANNING_SLOTS_CAPACITY: int = 0

def _plan_quality_from_task_ids(*, task_ids: list[str], mode: str, planning_policy: dict[str, Any], team_id: str | None) -> tuple[bool, str]:
    if not task_ids:
        return False, "no_tasks"
    subtasks: list[dict[str, Any]] = []
    for tid in task_ids:
        task = _repos().task_repo.get_by_id(tid)
        if not task:
            continue
        subtasks.append(
            {
                "title": str(getattr(task, "title", "") or ""),
                "description": str(getattr(task, "description", "") or ""),
                "task_kind": str(
                    getattr(task, "task_kind", None)
                    or getattr(task, "task_type", None)
                    or ""
                ),
            }
        )
    if not subtasks:
        return False, "tasks_missing_payload"
    quality = get_planning_quality_service().evaluate(
        subtasks=subtasks,
        mode=mode,
        planning_policy=planning_policy,
        team_id=team_id,
    )
    contract = resolve_planning_contract(mode=mode, planning_policy=planning_policy)
    contract_validation = get_planning_validation_service().validate_subtasks(
        subtasks=subtasks,
        contract=contract,
    )
    if contract_validation.ok:
        return quality.ok, quality.reason
    contract_reason = "|".join(list(contract_validation.error_codes) or ["planning_contract_failed"])
    if contract_validation.missing_task_kinds:
        contract_reason = (
            f"{contract_reason}|missing_task_kinds:{','.join(contract_validation.missing_task_kinds)}"
        )
    return False, contract_reason


# Import sub-modules so their route decorators register on goals_bp
import agent.routes.tasks.goals_query_routes  # noqa: E402
import agent.routes.tasks.goals_planning_routes  # noqa: E402


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
    mode_data = dict(payload.mode_data or {})
    goal_text = str(payload.goal or "").strip()

    if goal_text:
        if mode_id == "new_software_project" and not str(mode_data.get("project_idea") or "").strip():
            mode_data["project_idea"] = goal_text
        if mode_id == "project_evolution" and not str(mode_data.get("change_goal") or "").strip():
            mode_data["change_goal"] = goal_text

    mode_data = _goal_service().normalize_mode_data(mode_id, mode_data)

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
    config_profile = str(execution_preferences.get("config_profile") or "").strip() or None
    config_overrides = execution_preferences.get("config_overrides")
    if config_overrides is None:
        config_overrides = {}
    if not isinstance(config_overrides, dict):
        return api_response(status="error", message="invalid_config_overrides", code=400)
    unknown_override_keys = sorted(k for k in config_overrides if k not in ALLOWED_GOAL_CONFIG_KEYS)
    if unknown_override_keys:
        return api_response(
            status="error",
            message="invalid_goal_config_key",
            data={"unknown_keys": unknown_override_keys},
            code=400,
        )
    if config_profile and get_config_profile_service().get_profile(config_profile) is None:
        return api_response(status="error", message="unknown_config_profile", code=400)

    agent_cfg = dict(current_app.config.get("AGENT_CONFIG", {}) or {})
    goal_scoped_config_enabled = bool(agent_cfg.get("goal_scoped_config_enabled", True))
    goal_scoped_config_enforce_snapshot = bool(agent_cfg.get("goal_scoped_config_enforce_snapshot", False))
    execution_preferences["config_profile"] = config_profile
    execution_preferences["config_overrides"] = dict(config_overrides)
    if goal_scoped_config_enabled:
        resolver = get_goal_config_resolver_service()
        resolution = resolver.resolve(
            system_config=agent_cfg,
            profile_id=config_profile,
            goal_overrides=config_overrides,
        )
        execution_preferences["config_snapshot"] = dict(resolution.config_snapshot)
        execution_preferences["config_snapshot_provenance"] = dict(resolution.provenance)
        execution_preferences["config_snapshot_checksum"] = str(resolution.checksum)
        execution_preferences["config_snapshot_hash"] = str(resolution.checksum)
        execution_preferences["config_redaction_summary"] = dict(resolution.redaction_summary)
    elif goal_scoped_config_enforce_snapshot:
        return api_response(status="error", message="goal_scoped_config_snapshot_required", code=412)
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

    _cancel_stale_planning_goals(actor=_current_username())

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
        target_status="planning_queued",
        reason="goal_accepted_for_planning_queue",
        readiness=readiness,
    )
    planning_context = {
        "goal_text": goal_text,
        "mode_context": mode_context,
        "effective": effective,
        "mode_id": mode_id,
        "readiness": dict(readiness or {}),
    }
    created_task_ids: list[str] = []
    plan_id: str | None = None
    if current_app.testing:
        from agent.routes.tasks.goals_planning_routes import _run_goal_planning_background_impl
        _run_goal_planning_background_impl(goal_id=goal_record.id, context=planning_context)
        refreshed_goal = _repos().goal_repo.get_by_id(goal_record.id)
        if refreshed_goal is not None:
            goal_record = refreshed_goal
        created_task_ids = [str(task.id) for task in (_repos().task_repo.get_by_goal_id(goal_record.id) or []) if str(task.id or "").strip()]
        plans = _repos().plan_repo.get_by_goal_id(goal_record.id) or []
        if plans:
            plan_id = str(plans[0].id or "") or None
    else:
        _app = current_app._get_current_object()
        thread = threading.Thread(
            target=_run_goal_planning_background,
            kwargs={"goal_id": goal_record.id, "context": planning_context, "app": _app},
            daemon=True,
            name=f"goal-planning-{goal_record.id[:8]}",
        )
        thread.start()
    return api_response(
        data={
            "goal": _goal_service().serialize_goal(goal_record),
            "created_task_ids": created_task_ids,
            "plan_id": plan_id,
            "planning_status": "queued",
            "workflow": {
                "defaults": defaults,
                "overrides": overrides,
                "effective": effective,
                "provenance": provenance,
            },
            "readiness": readiness,
        },
        code=201,
    )


# ── Re-exports for test compatibility ──────────────────────────────

from agent.routes.tasks.goals_helpers import (  # noqa: E402
    _can_access_goal,
    _GOAL_ACTIVE_PLANNING_IDS,
    _GOAL_ACTIVE_PLANNING_LOCK,
    _is_soft_planning_quality_failure,
    _looks_like_software_goal,
    _mark_started_planning_runs_failed,
    _maybe_recover_stalled_planning_goal,
    _team_scope_allows,
)
from agent.routes.tasks.goals_planning_routes import (  # noqa: E402
    _acquire_planning_slot,
    _clear_planning_lease,
    _normalize_planning_slot_capacity,
    _planning_slot_capacity_from_config,
    _release_planning_slot,
    _run_goal_planning_background,
    _run_goal_planning_background_impl,
    _set_planning_lease,
    _start_planning_heartbeat,
)
from agent.routes.tasks.goals_query_routes import (  # noqa: E402
    goal_gate_human_decision,
    goal_workflow_status,
)
