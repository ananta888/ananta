import time
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from typing import Any

from flask import Blueprint, current_app, g, request

from agent.auth import check_auth
from agent.common.audit import log_audit
from agent.common.errors import api_response
from agent.config import settings
from agent.db_models import GoalDB
from agent.models import GoalCreateRequest, GoalPlanNodePatchRequest, GoalProvisionRequest
from agent.services.goal_execution_contract_service import get_goal_execution_contract_service
from agent.services.goal_purge_service import get_goal_purge_service
from agent.services.goal_config_resolver_service import ALLOWED_GOAL_CONFIG_KEYS, get_goal_config_resolver_service
from agent.services.goal_config_runtime_service import get_goal_config_runtime_service
from agent.services.config_profile_service import get_config_profile_service
from agent.services.product_event_service import record_product_event
from agent.services.instruction_layer_service import get_instruction_layer_service
from agent.services.repository_registry import get_repository_registry
from agent.services.service_registry import get_core_services
from agent.services.planning_quality_service import get_planning_quality_service
from agent.services.planning_telemetry_service import get_planning_telemetry_service
from agent.services.goal_planning_intent_service import get_goal_planning_intent_service
from agent.utils import validate_request
from agent.planning_reason_codes import (
    PLANNING_SLOT_TIMEOUT,
    PLANNING_BACKGROUND_TIMEOUT,
    PLANNING_BACKGROUND_EXCEPTION,
    PLANNING_DEADLINE_GUARD_TIMEOUT,
    PLANNING_STALE_RECOVERED,
)

goals_bp = Blueprint("tasks_goals", __name__)
_GOAL_ACTIVE_PLANNING_LOCK = threading.Lock()
_GOAL_ACTIVE_PLANNING_IDS: set[str] = set()
_PLANNING_SLOTS_LOCK = threading.Lock()
_PLANNING_SLOTS: threading.Semaphore | None = None
_PLANNING_SLOTS_CAPACITY: int = 0


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
    intent = get_goal_planning_intent_service().classify(goal_text=text, mode="generic")
    return str(intent.get("goal_type") or "") == "software_project"


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
    return quality.ok, quality.reason


def _is_soft_planning_quality_failure(*, quality_reason: str) -> bool:
    """Allow non-critical category misses without hard-failing full software-goal runs.

    We keep hard blockers (e.g. too few tasks, generic-only plans), but tolerate
    missing analysis/review category hints for otherwise executable plans.
    """
    normalized = str(quality_reason or "").strip().lower()
    if not normalized or normalized == "ok":
        return False
    parts = [p for p in normalized.split("|") if p]
    if not parts:
        return False
    if any(p.startswith("too_few_tasks:") for p in parts):
        return False
    # Overly strict generic-task detection on local/smaller models should not
    # hard-fail otherwise executable plans.
    non_soft_allowed_prefixes = ("missing_categories:", "too_many_generic_tasks:")
    if any(not p.startswith(non_soft_allowed_prefixes) for p in parts):
        return False
    has_missing = any(p.startswith("missing_categories:") for p in parts)
    has_generic = any(p.startswith("too_many_generic_tasks:") for p in parts)
    if has_generic:
        return True
    if has_missing:
        missing_parts = [p for p in parts if p.startswith("missing_categories:")]
        missing_blob = ",".join(p.removeprefix("missing_categories:") for p in missing_parts)
        missing_entries = [entry.strip() for entry in missing_blob.split(",") if entry.strip()]
        return bool(missing_entries)
    return False


def _mark_started_planning_runs_failed(*, goal_id: str, reason: str) -> int:
    updated = 0
    runs = list(_repos().planning_run_repo.get_by_goal_id(goal_id, limit=50) or [])
    for run in runs:
        if str(getattr(run, "goal_id", "") or "").strip() != goal_id:
            continue
        if str(getattr(run, "status", "") or "").strip().lower() != "started":
            continue
        get_planning_telemetry_service().update_run(
            run,
            status="failed",
            error_classification=str(reason or "planning_failed"),
            validation_errors=[str(reason or "planning_failed")],
        )
        updated += 1
    return updated


def _maybe_recover_stalled_planning_goal(goal: GoalDB) -> GoalDB:
    status = str(getattr(goal, "status", "") or "").strip().lower()
    if status not in {"planning", "planning_queued", "planning_running"}:
        return goal
    goal_id = str(getattr(goal, "id", "") or "").strip()
    if not goal_id:
        return goal
    # Never trigger recovery re-planning from read-path polling while planning
    # is queued/running. The background planner thread is the single owner.
    if status in {"planning_queued", "planning_running"}:
        with _GOAL_ACTIVE_PLANNING_LOCK:
            if goal_id in _GOAL_ACTIVE_PLANNING_IDS:
                return goal
        return goal
    now_ts = time.time()
    updated_at = float(getattr(goal, "updated_at", 0.0) or 0.0)
    if updated_at and (now_ts - updated_at) < 30:
        return goal

    # Hard timeout fallback: if planning_run for this goal is stuck in "started"
    # for too long, force terminal failure to avoid endless planning_running.
    try:
        planning_runs = [r for r in _repos().planning_run_repo.get_by_goal_id(goal_id, limit=20) if str(getattr(r, "goal_id", "") or "") == goal_id]
        started_runs = [r for r in planning_runs if str(getattr(r, "status", "") or "") == "started"]
        if started_runs:
            latest_started = sorted(started_runs, key=lambda x: float(getattr(x, "updated_at", 0.0) or 0.0), reverse=True)[0]
            started_age = now_ts - float(getattr(latest_started, "updated_at", 0.0) or 0.0)
            # If planning is actively running, never start recovery planning from
            # read-path polling endpoints (e.g. /goals/<id>/detail). This avoids
            # duplicate planning calls and duplicate task materialization.
            if started_age <= 180:
                return goal
            if started_age > 180:
                _mark_started_planning_runs_failed(
                    goal_id=goal_id,
                    reason="planning_stuck_timeout_recovery",
                )
                return _services().goal_lifecycle_service.transition_goal(
                    goal,
                    target_status="failed",
                    reason="planning_stuck_timeout_recovery",
                    readiness=dict(getattr(goal, "readiness", None) or {}),
                )
    except Exception:
        pass

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


@goals_bp.route("/goals/<goal_id>/effective-config", methods=["GET"])
@check_auth
def get_goal_effective_config(goal_id: str):
    goal = _repos().goal_repo.get_by_id(goal_id)
    if not goal or not _can_access_goal(goal):
        return api_response(status="error", message="not_found", code=404)
    execution_preferences = dict(getattr(goal, "execution_preferences", None) or {})
    snapshot = dict(execution_preferences.get("config_snapshot") or {})
    return api_response(
        data={
            "goal_id": str(getattr(goal, "id", "") or ""),
            "config_snapshot": snapshot,
            "provenance": dict(execution_preferences.get("config_snapshot_provenance") or snapshot.get("provenance") or {}),
            "config_checksum": str(execution_preferences.get("config_snapshot_checksum") or "").strip() or None,
            "config_snapshot_hash": str(execution_preferences.get("config_snapshot_hash") or "").strip() or None,
            "redaction_summary": dict(execution_preferences.get("config_redaction_summary") or {}),
            "goal_config_source": "snapshot" if snapshot else "global_fallback",
        }
    )


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


@goals_bp.route("/goals/<goal_id>/purge", methods=["DELETE"])
@check_auth
def purge_goal(goal_id: str):
    goal = _repos().goal_repo.get_by_id(goal_id)
    if not goal:
        # Idempotent: already purged is not an error for the caller.
        return api_response(data={"goal_id": goal_id, "already_deleted": True, "deleted_total": 0})
    if not _can_access_goal(goal):
        return api_response(status="error", message="not_found", code=404)
    if not _is_admin_request():
        return api_response(status="error", message="forbidden", code=403)
    include_prompt_traces = str(request.args.get("include_prompt_traces", "1")).strip().lower() not in {"0", "false", "no"}
    result = get_goal_purge_service().purge_goal(goal_id, include_prompt_traces=include_prompt_traces)
    if result is None:
        # Race: goal disappeared between the check and the purge — treat as already deleted.
        return api_response(data={"goal_id": goal_id, "already_deleted": True, "deleted_total": 0})
    log_audit(
        "goal_purged",
        {
            "goal_id": goal_id,
            "trace_id": getattr(goal, "trace_id", None),
            "include_prompt_traces": include_prompt_traces,
            "deleted": result.to_dict(),
        },
    )
    return api_response(data=result.to_dict())


@goals_bp.route("/goals/<goal_id>/kill-requests", methods=["POST"])
@check_auth
def kill_goal_requests(goal_id: str):
    """Abort all in-flight LM Studio HTTP requests for a goal without cancelling the goal itself."""
    if not _is_admin_request():
        return api_response(status="error", message="forbidden", code=403)
    goal_id_norm = str(goal_id or "").strip()
    if not goal_id_norm:
        return api_response(status="error", message="goal_id required", code=400)
    from agent.services.lmstudio_request_registry import cancel_goal, active_counts
    killed = cancel_goal(goal_id_norm)
    remaining = active_counts()
    return api_response(data={"goal_id": goal_id_norm, "sessions_killed": killed, "active_counts": remaining})


@goals_bp.route("/goals/kill-all-requests", methods=["POST"])
@check_auth
def kill_all_requests():
    """Abort all in-flight LM Studio HTTP requests across all goals."""
    if not _is_admin_request():
        return api_response(status="error", message="forbidden", code=403)
    from agent.services.lmstudio_request_registry import cancel_all, active_counts
    killed = cancel_all()
    return api_response(data={"sessions_killed": killed, "active_counts": active_counts()})


@goals_bp.route("/goals/planning/health", methods=["GET"])
@check_auth
def planning_health():
    """PRI-012: Planning health summary — slot state, stale counts, circuit breaker state."""
    if not _is_admin_request():
        return api_response(status="error", message="forbidden", code=403)

    from sqlmodel import Session, select, func
    from agent.database import engine
    from agent.db_models import GoalDB as _GoalDB
    from agent.llm_integration import get_circuit_breaker_state

    now = time.time()
    stale_count = 0
    queued_count = 0
    running_count = 0

    try:
        with Session(engine) as session:
            for status_val, count in session.exec(
                select(_GoalDB.status, func.count(_GoalDB.id))
                .where(_GoalDB.status.in_(["planning_queued", "planning_running"]))
                .group_by(_GoalDB.status)
            ).all():
                if status_val == "planning_queued":
                    queued_count = int(count)
                elif status_val == "planning_running":
                    running_count = int(count)
            stale_count = session.exec(
                select(func.count(_GoalDB.id)).where(
                    _GoalDB.status.in_(["planning_running", "planning_queued"]),
                    _GoalDB.planning_lease_expires_at != None,  # noqa: E711
                    _GoalDB.planning_lease_expires_at < now,
                )
            ).one()
    except Exception as exc:
        return api_response(status="error", message=f"db_query_failed:{type(exc).__name__}", code=500)

    with _PLANNING_SLOTS_LOCK:
        slot_capacity = int(_PLANNING_SLOTS_CAPACITY or 0)
        slots_available = _PLANNING_SLOTS._value if _PLANNING_SLOTS is not None else slot_capacity  # type: ignore[attr-defined]

    agent_cfg = current_app.config.get("AGENT_CONFIG") or {}
    lmstudio_cfg = (agent_cfg.get("llm_config") or {})
    provider = str(lmstudio_cfg.get("provider") or agent_cfg.get("default_provider") or "unknown")

    return api_response(data={
        "planning_slots": {
            "capacity": slot_capacity,
            "available": int(slots_available),
            "in_use": max(0, slot_capacity - int(slots_available)),
        },
        "goals": {
            "queued": queued_count,
            "running": running_count,
            "stale_expired_lease": int(stale_count),
        },
        "circuit_breaker": get_circuit_breaker_state(provider),
        "timestamp": now,
    })


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

    # Preserve explicit goal intent for guided modes when mode_data is incomplete.
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

    # PRI-006: cancel stale planning_running/queued goals before starting a new one.
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
            "planning_status": "queued",
            "workflow": {
                "defaults": defaults,
                "overrides": overrides,
                "effective": effective,
                "provenance": provenance,
            },
            "readiness": readiness,
        },
        code=202,
    )


def _cancel_stale_planning_goals(actor: str = "preflight") -> int:
    """Mark planning_running/queued goals with an expired lease as failed (PRI-006)."""
    try:
        from sqlalchemy import text
        from agent.database import engine
        now = time.time()
        with engine.connect() as conn:
            result = conn.execute(
                text(
                    "UPDATE goals SET status = 'failed', planning_lease_expires_at = NULL, updated_at = :now"
                    " WHERE status IN ('planning_running', 'planning_queued')"
                    " AND planning_lease_expires_at IS NOT NULL"
                    " AND planning_lease_expires_at < :now"
                ),
                {"now": now},
            )
            conn.commit()
            cancelled = int(result.rowcount or 0)
        if cancelled:
            try:
                current_app.logger.warning(
                    "planning_preflight_stale_cancelled count=%s actor=%s", cancelled, actor
                )
            except Exception:
                pass
        return cancelled
    except Exception:
        return 0


def _run_goal_planning_background(*, goal_id: str, context: dict[str, Any], app: Any) -> None:
    with app.app_context():
        _run_goal_planning_background_impl(goal_id=goal_id, context=context)


def _normalize_planning_slot_capacity(raw: Any) -> int:
    try:
        cap = int(raw if raw is not None else 1)
    except (TypeError, ValueError):
        cap = 1
    return max(1, min(cap, 32))


def _planning_slot_capacity_from_config() -> int:
    cfg = dict(current_app.config.get("AGENT_CONFIG", {}) or {})
    planning_policy = cfg.get("planning_policy") if isinstance(cfg.get("planning_policy"), dict) else {}
    return _normalize_planning_slot_capacity(planning_policy.get("parallel_goal_planning_max_concurrency", 1))


def _acquire_planning_slot(*, timeout_s: int, capacity: int | None = None) -> tuple[bool, int]:
    global _PLANNING_SLOTS, _PLANNING_SLOTS_CAPACITY
    normalized_capacity = _normalize_planning_slot_capacity(capacity) if capacity is not None else _planning_slot_capacity_from_config()
    with _PLANNING_SLOTS_LOCK:
        if _PLANNING_SLOTS is None or _PLANNING_SLOTS_CAPACITY != normalized_capacity:
            _PLANNING_SLOTS = threading.Semaphore(normalized_capacity)
            _PLANNING_SLOTS_CAPACITY = normalized_capacity
        semaphore = _PLANNING_SLOTS
    acquired = bool(semaphore.acquire(timeout=max(1, int(timeout_s))))
    return acquired, normalized_capacity


def _release_planning_slot() -> None:
    with _PLANNING_SLOTS_LOCK:
        semaphore = _PLANNING_SLOTS
    if semaphore is not None:
        semaphore.release()


_PLANNING_LEASE_TTL_S = 90  # seconds per heartbeat interval


def _set_planning_lease(goal_id: str, ttl_s: int = _PLANNING_LEASE_TTL_S) -> None:
    """Write/renew planning_lease_expires_at using a direct UPDATE to avoid session conflicts."""
    try:
        from sqlalchemy import text
        from agent.database import engine
        expires_at = time.time() + ttl_s
        now = time.time()
        with engine.connect() as conn:
            conn.execute(
                text(
                    "UPDATE goals SET planning_lease_expires_at = :exp, updated_at = :now"
                    " WHERE id = :gid AND status = 'planning_running'"
                ),
                {"exp": expires_at, "now": now, "gid": goal_id},
            )
            conn.commit()
    except Exception:
        pass


def _clear_planning_lease(goal_id: str) -> None:
    try:
        from sqlalchemy import text
        from agent.database import engine
        with engine.connect() as conn:
            conn.execute(
                text("UPDATE goals SET planning_lease_expires_at = NULL, updated_at = :now WHERE id = :gid"),
                {"now": time.time(), "gid": goal_id},
            )
            conn.commit()
    except Exception:
        pass


def _start_planning_heartbeat(*, goal_id: str, stop_event: threading.Event, interval_s: int = _PLANNING_LEASE_TTL_S // 2) -> threading.Thread:
    """Periodically renew the planning lease while planning_running."""
    def _beat() -> None:
        while not stop_event.wait(timeout=max(5, interval_s)):
            _set_planning_lease(goal_id, ttl_s=_PLANNING_LEASE_TTL_S)

    thread = threading.Thread(target=_beat, daemon=True, name=f"planning-heartbeat-{goal_id[:8]}")
    thread.start()
    return thread


def _start_planning_deadline_guard(*, goal_id: str, app: Any, timeout_s: int) -> None:
    def _guard() -> None:
        try:
            app.logger.warning("planning_deadline_guard_started goal_id=%s timeout_s=%s", goal_id, int(timeout_s))
        except Exception:
            pass
        time.sleep(max(10, int(timeout_s)))
        with app.app_context():
            goal = _repos().goal_repo.get_by_id(goal_id)
            if not goal:
                return
            status = str(getattr(goal, "status", "") or "").strip().lower()
            # Guard applies only while planning is still in progress. Once the
            # goal leaves planning states, this watchdog must no longer force
            # a terminal failure.
            if status != "planning_running":
                return
            _mark_started_planning_runs_failed(
                goal_id=goal_id,
                reason=PLANNING_DEADLINE_GUARD_TIMEOUT,
            )
            _services().goal_lifecycle_service.transition_goal(
                goal,
                target_status="failed",
                reason=PLANNING_DEADLINE_GUARD_TIMEOUT,
                readiness=dict(getattr(goal, "readiness", None) or {}),
            )
            try:
                app.logger.error("planning_deadline_guard_timeout goal_id=%s", goal_id)
            except Exception:
                pass
            record_product_event(
                "goal_planning_failed",
                actor="auto_planner",
                details={"reason": PLANNING_DEADLINE_GUARD_TIMEOUT, "timeout_seconds": int(timeout_s)},
                goal_id=goal_id,
                trace_id=str(getattr(goal, "trace_id", "") or ""),
                plan_id=None,
            )

    thread = threading.Thread(target=_guard, daemon=True, name=f"planning-deadline-guard-{goal_id[:8]}")
    thread.start()


def _run_goal_planning_background_impl(*, goal_id: str, context: dict[str, Any]) -> None:
    with _GOAL_ACTIVE_PLANNING_LOCK:
        if goal_id in _GOAL_ACTIVE_PLANNING_IDS:
            try:
                current_app.logger.warning("goal_planning_skip_duplicate_inflight goal_id=%s", goal_id)
            except Exception:
                pass
            return
        _GOAL_ACTIVE_PLANNING_IDS.add(goal_id)
    try:
        goal_record = _repos().goal_repo.get_by_id(goal_id)
        if not goal_record:
            return
        readiness = dict(context.get("readiness") or {})
        from agent.routes.tasks.auto_planner import auto_planner
        effective = dict(context.get("effective") or {})
        overrides = dict(getattr(goal_record, "workflow_overrides", None) or {})

        _live_planning_policy = (current_app.config.get("AGENT_CONFIG") or {}).get("planning_policy") or {}
        goal_scoped_cfg = get_goal_config_runtime_service().get_effective_config(
            goal_id=goal_record.id,
            task_id=None,
        ).config
        goal_scoped_planning_policy = (
            (goal_scoped_cfg or {}).get("planning_policy")
            if isinstance(goal_scoped_cfg, dict)
            else None
        )
        _resolved_pp = goal_scoped_planning_policy or effective.get("planning_policy") or _live_planning_policy
        _pp_timeout = _resolved_pp.get("timeout_seconds")
        _pp_queue_wait = _resolved_pp.get("queue_wait_timeout_seconds")
        _pp_parallel = _resolved_pp.get("parallel_goal_planning_max_concurrency")
        # PRI-005: execute timeout (LLM call) and queue-wait timeout are now separate.
        # queue_wait_timeout_seconds defaults to execute timeout when not set.
        planning_execute_timeout_s = int(max(30, _pp_timeout if _pp_timeout is not None else 300))
        planning_queue_wait_timeout_s = int(max(10, _pp_queue_wait if _pp_queue_wait is not None else planning_execute_timeout_s))
        # Keep the outer background wait slightly above the inner planner timeout
        # so planning_service can return structured timeout diagnostics first
        # (resolve_subtasks_timeout) instead of being masked as a background timeout.
        outer_planning_timeout_s = planning_execute_timeout_s + 45
        planning_parallel_slots = _normalize_planning_slot_capacity(_pp_parallel)
        app_obj = current_app._get_current_object()

        def _run_plan_goal_with_app_context():
            with app_obj.app_context():
                return auto_planner.plan_goal(
                    goal=str(context.get("goal_text") or goal_record.goal or ""),
                    context=context.get("mode_context"),
                    team_id=effective.get("routing", {}).get("team_id"),
                    create_tasks=bool(effective.get("planning", {}).get("create_tasks", True)),
                    use_template=bool(effective.get("planning", {}).get("use_template", True)),
                    use_repo_context=bool(effective.get("planning", {}).get("use_repo_context", True)),
                    goal_id=goal_record.id,
                    goal_trace_id=goal_record.trace_id,
                    mode=goal_record.mode,
                    mode_data=goal_record.mode_data,
                )

        slot_acquired = False
        _queue_wait_started_at = time.monotonic()
        try:
            slot_acquired, planning_slot_capacity = _acquire_planning_slot(
                timeout_s=planning_queue_wait_timeout_s,
                capacity=planning_parallel_slots,
            )
            queue_wait_elapsed_s = round(time.monotonic() - _queue_wait_started_at, 2)
            if not slot_acquired:
                _services().goal_lifecycle_service.transition_goal(
                    goal_record,
                    target_status="failed",
                    reason=PLANNING_SLOT_TIMEOUT,
                    readiness=readiness,
                )
                record_product_event(
                    "goal_planning_failed",
                    actor="auto_planner",
                    details={
                        "reason": PLANNING_SLOT_TIMEOUT,
                        "queue_wait_timeout_seconds": planning_queue_wait_timeout_s,
                        "queue_wait_elapsed_seconds": queue_wait_elapsed_s,
                        "source": goal_record.source,
                        "mode": goal_record.mode,
                    },
                    goal_id=goal_record.id,
                    trace_id=goal_record.trace_id,
                    plan_id=None,
                )
                return
            goal_record = _services().goal_lifecycle_service.transition_goal(
                goal_record,
                target_status="planning_running",
                reason="planning_background_started",
                readiness=readiness,
            )
            # PRI-004: set initial lease and start heartbeat thread.
            _set_planning_lease(goal_record.id)
            _heartbeat_stop = threading.Event()
            _start_planning_heartbeat(goal_id=goal_record.id, stop_event=_heartbeat_stop)
            current_app.logger.warning(
                "goal_planning_invoke_start goal_id=%s execute_timeout_s=%s outer_timeout_s=%s queue_wait_s=%s mode=%s slot_capacity=%s",
                goal_record.id,
                planning_execute_timeout_s,
                outer_planning_timeout_s,
                queue_wait_elapsed_s,
                str(goal_record.mode or "generic"),
                planning_slot_capacity,
            )
            _start_planning_deadline_guard(
                goal_id=goal_record.id,
                app=current_app._get_current_object(),
                timeout_s=max(60, planning_execute_timeout_s + 30),
            )
            pool = ThreadPoolExecutor(max_workers=1)
            try:
                future = pool.submit(_run_plan_goal_with_app_context)
                result = future.result(timeout=outer_planning_timeout_s)
            finally:
                _heartbeat_stop.set()
                _clear_planning_lease(goal_record.id)
                pool.shutdown(wait=False, cancel_futures=True)
            current_app.logger.warning(
                "goal_planning_invoke_done goal_id=%s created=%s error=%s",
                goal_record.id,
                len(list(result.get("created_task_ids") or [])) if isinstance(result, dict) else -1,
                (result.get("error") if isinstance(result, dict) else "invalid_result"),
            )
        except FutureTimeoutError:
            _mark_started_planning_runs_failed(
                goal_id=goal_record.id,
                reason=f"{PLANNING_BACKGROUND_TIMEOUT}:{planning_execute_timeout_s}s",
            )
            _services().goal_lifecycle_service.transition_goal(
                goal_record,
                target_status="failed",
                reason=PLANNING_BACKGROUND_TIMEOUT,
                readiness=readiness,
            )
            record_product_event(
                "goal_planning_failed",
                actor="auto_planner",
                details={
                    "reason": PLANNING_BACKGROUND_TIMEOUT,
                    "execute_timeout_seconds": planning_execute_timeout_s,
                    "source": goal_record.source,
                    "mode": goal_record.mode,
                },
                goal_id=goal_record.id,
                trace_id=goal_record.trace_id,
                plan_id=None,
            )
            return
        except Exception as exc:
            current_app.logger.exception("background_goal_planning_failed goal_id=%s", goal_record.id)
            _mark_started_planning_runs_failed(
                goal_id=goal_record.id,
                reason=f"planning_background_exception:{type(exc).__name__}",
            )
            _services().goal_lifecycle_service.transition_goal(
                goal_record,
                target_status="failed",
                reason=f"planning_background_exception:{type(exc).__name__}",
                readiness=readiness,
            )
            record_product_event(
                "goal_planning_failed",
                actor="auto_planner",
                details={
                    "reason": f"planning_background_exception:{type(exc).__name__}",
                    "error": str(exc)[:240],
                    "source": goal_record.source,
                    "mode": goal_record.mode,
                },
                goal_id=goal_record.id,
                trace_id=goal_record.trace_id,
                plan_id=None,
            )
            return
        finally:
            if slot_acquired:
                _release_planning_slot()

        current_app.logger.debug(f"plan result: {result}")
        if result.get("error"):
            _services().goal_lifecycle_service.transition_goal(
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
            return

        created_task_ids = list(result.get("created_task_ids") or [])
        create_tasks_enabled = bool(effective.get("planning", {}).get("create_tasks", True))
        software_goal = _looks_like_software_goal(str(context.get("goal_text") or ""))
        if (
            create_tasks_enabled
            and not created_task_ids
            and str(context.get("mode_id") or "generic") == "generic"
            and software_goal
        ):
            retry_mode = "new_software_project"
            retry_mode_data = dict(goal_record.mode_data or {})
            retry_mode_data.setdefault("project_idea", str(context.get("goal_text") or ""))
            retry_result = auto_planner.plan_goal(
                goal=str(context.get("goal_text") or goal_record.goal or ""),
                context=context.get("mode_context"),
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

        if (
            create_tasks_enabled
            and created_task_ids
            and str(context.get("mode_id") or "generic") == "generic"
            and software_goal
        ):
            quality_ok, quality_reason = _plan_quality_from_task_ids(
                task_ids=created_task_ids,
                mode="new_software_project",
                planning_policy=(effective.get("planning_policy") if isinstance(effective.get("planning_policy"), dict) else _live_planning_policy),
                team_id=str(effective.get("routing", {}).get("team_id") or "") or None,
            )
        else:
            quality_ok, quality_reason = True, "not_software_goal_or_disabled"

        if create_tasks_enabled and not created_task_ids:
            _services().goal_lifecycle_service.transition_goal(
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
            return

        if create_tasks_enabled and software_goal and not quality_ok and not _is_soft_planning_quality_failure(quality_reason=quality_reason):
            _services().goal_lifecycle_service.transition_goal(
                goal_record,
                target_status="failed",
                reason="planning_insufficient_task_detail",
                readiness=readiness,
            )
            record_product_event(
                "goal_planning_failed",
                actor="auto_planner",
                details={
                    "reason": "planning_insufficient_task_detail",
                    "quality_reason": quality_reason,
                    "task_count": len(created_task_ids),
                    "source": goal_record.source,
                    "mode": goal_record.mode,
                },
                goal_id=goal_record.id,
                trace_id=goal_record.trace_id,
                plan_id=result.get("plan_id"),
            )
            return
        if create_tasks_enabled and software_goal and not quality_ok:
            record_product_event(
                "goal_planning_quality_soft_failed",
                actor="auto_planner",
                details={
                    "reason": "planning_quality_soft_failed",
                    "quality_reason": quality_reason,
                    "task_count": len(created_task_ids),
                    "source": goal_record.source,
                    "mode": goal_record.mode,
                },
                goal_id=goal_record.id,
                trace_id=goal_record.trace_id,
                plan_id=result.get("plan_id"),
            )

        _services().goal_lifecycle_service.transition_goal(
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
    finally:
        with _GOAL_ACTIVE_PLANNING_LOCK:
            _GOAL_ACTIVE_PLANNING_IDS.discard(goal_id)
