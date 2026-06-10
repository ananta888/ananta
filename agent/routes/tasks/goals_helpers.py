import threading
import time
from typing import Any

from flask import current_app, g

from agent.db_models import GoalDB
from agent.services.planning_telemetry_service import get_planning_telemetry_service
from agent.services.goal_planning_intent_service import get_goal_planning_intent_service
from agent.services.repository_registry import get_repository_registry
from agent.services.service_registry import get_core_services


# Shared planning state (used by planning_routes and query_routes via helpers)
_GOAL_ACTIVE_PLANNING_LOCK = threading.Lock()
_GOAL_ACTIVE_PLANNING_IDS: set[str] = set()


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


def _is_soft_planning_quality_failure(*, quality_reason: str) -> bool:
    normalized = str(quality_reason or "").strip().lower()
    if not normalized or normalized == "ok":
        return False
    parts = [p for p in normalized.split("|") if p]
    if not parts:
        return False
    if any(p.startswith("too_few_tasks:") for p in parts):
        return False
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
    if status in {"planning_queued", "planning_running"}:
        with _GOAL_ACTIVE_PLANNING_LOCK:
            if goal_id in _GOAL_ACTIVE_PLANNING_IDS:
                return goal
        return goal
    now_ts = time.time()
    updated_at = float(getattr(goal, "updated_at", 0.0) or 0.0)
    if updated_at and (now_ts - updated_at) < 30:
        return goal

    try:
        planning_runs = [r for r in _repos().planning_run_repo.get_by_goal_id(goal_id, limit=20) if str(getattr(r, "goal_id", "") or "") == goal_id]
        started_runs = [r for r in planning_runs if str(getattr(r, "status", "") or "") == "started"]
        if started_runs:
            latest_started = sorted(started_runs, key=lambda x: float(getattr(x, "updated_at", 0.0) or 0.0), reverse=True)[0]
            started_age = now_ts - float(getattr(latest_started, "updated_at", 0.0) or 0.0)
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


def _cancel_stale_planning_goals(actor: str = "preflight") -> int:
    from sqlalchemy import text

    from agent.database import engine

    try:
        now = time.time()
        with engine.begin() as conn:
            result = conn.execute(
                text(
                    """
                    UPDATE goals
                    SET status = 'failed',
                        planning_lease_expires_at = NULL,
                        updated_at = :now
                    WHERE status IN ('planning_running', 'planning_queued')
                      AND planning_lease_expires_at IS NOT NULL
                      AND planning_lease_expires_at < :now
                    """
                ),
                {"now": now},
            )
            cancelled = int(result.rowcount or 0)
        if cancelled:
            try:
                current_app.logger.warning(
                    "planning_preflight_stale_cancelled count=%s actor=%s", cancelled, actor
                )
            except Exception:
                pass
        return cancelled
    except Exception as exc:
        try:
            current_app.logger.exception("planning_preflight_stale_cancel_failed actor=%s error=%s", actor, exc)
        except Exception:
            pass
        return 0
