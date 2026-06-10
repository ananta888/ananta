import threading
import time
from concurrent.futures import TimeoutError as FutureTimeoutError
from typing import Any

from flask import current_app, request
from sqlmodel import Session, select, func

from agent.auth import check_auth
from agent.common.audit import log_audit
from agent.common.errors import api_response
from agent.config import settings
from agent.planning_reason_codes import (
    PLANNING_BACKGROUND_TIMEOUT,
    PLANNING_SLOT_TIMEOUT,
)
from agent.routes.tasks.goals import goals_bp
from agent.routes.tasks.goals_helpers import (
    _can_access_goal,
    _goal_service,
    _GOAL_ACTIVE_PLANNING_IDS,
    _GOAL_ACTIVE_PLANNING_LOCK,
    _is_admin_request,
    _is_soft_planning_quality_failure,
    _looks_like_software_goal,
    _mark_started_planning_runs_failed,
    _repos,
    _services,
)
import agent.routes.tasks.goals as _goals_mod

_PLANNING_LEASE_TTL_S = 90


@goals_bp.route("/goals/planning/health", methods=["GET"])
@check_auth
def planning_health():
    if not _is_admin_request():
        return api_response(status="error", message="forbidden", code=403)

    from sqlmodel import Session, select, func
    from agent.database import engine
    from agent.db_models import GoalDB as _GoalDB
    from agent.llm_integration import get_circuit_breaker_state, get_rate_limit_state, get_provider_error_rate

    now = time.time()
    stale_count = 0
    queued_count = 0
    running_count = 0

    running_ages_s: list[float] = []
    oldest_queued_age_s: float | None = None
    by_profile: dict[str, dict[str, int]] = {}

    try:
        from sqlalchemy import text as _text
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
                    _GoalDB.planning_lease_expires_at != None,
                    _GoalDB.planning_lease_expires_at < now,
                )
            ).one()
            for (updated_at,) in session.exec(
                select(_GoalDB.updated_at).where(_GoalDB.status == "planning_running")
            ).all():
                if updated_at:
                    running_ages_s.append(round(now - float(updated_at), 1))
            oldest_queued_row = session.exec(
                select(func.min(_GoalDB.updated_at)).where(_GoalDB.status == "planning_queued")
            ).one()
            if oldest_queued_row:
                oldest_queued_age_s = round(now - float(oldest_queued_row), 1)
            try:
                profile_rows = session.exec(
                    _text(
                        "SELECT json_extract(execution_preferences, '$.config_profile') AS profile,"
                        " status, COUNT(*) AS cnt"
                        " FROM goals WHERE status IN ('planning_running','planning_queued')"
                        " GROUP BY profile, status"
                    )
                ).all()
                for (profile, status_val, cnt) in profile_rows:
                    key = str(profile or "unknown")
                    if key not in by_profile:
                        by_profile[key] = {"running": 0, "queued": 0}
                    if status_val == "planning_running":
                        by_profile[key]["running"] = int(cnt)
                    elif status_val == "planning_queued":
                        by_profile[key]["queued"] = int(cnt)
            except Exception:
                pass
    except Exception as exc:
        return api_response(status="error", message=f"db_query_failed:{type(exc).__name__}", code=500)

    with _goals_mod._PLANNING_SLOTS_LOCK:
        slot_capacity = int(_goals_mod._PLANNING_SLOTS_CAPACITY or 0)
        slots_available = _goals_mod._PLANNING_SLOTS._value if _goals_mod._PLANNING_SLOTS is not None else slot_capacity

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
        "running_ages_s": running_ages_s,
        "oldest_queued_age_s": oldest_queued_age_s,
        "circuit_breaker": get_circuit_breaker_state(provider),
        "rate_limit": get_rate_limit_state(provider),
        "provider_error_rate": get_provider_error_rate(provider),
        "by_profile": by_profile,
        "timestamp": now,
    })


@goals_bp.route("/goals/planning/recover-stale", methods=["POST"])
@check_auth
def planning_recover_stale():
    if not _is_admin_request():
        return api_response(status="error", message="forbidden", code=403)
    from agent.routes.tasks.goals_helpers import _cancel_stale_planning_goals
    cancelled = _cancel_stale_planning_goals(actor="recover_stale_api")
    return api_response(data={"cancelled": cancelled, "actor": "recover_stale_api"})


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
    normalized_capacity = _normalize_planning_slot_capacity(capacity) if capacity is not None else _planning_slot_capacity_from_config()
    with _goals_mod._PLANNING_SLOTS_LOCK:
        if _goals_mod._PLANNING_SLOTS is None or _goals_mod._PLANNING_SLOTS_CAPACITY != normalized_capacity:
            _goals_mod._PLANNING_SLOTS = threading.Semaphore(normalized_capacity)
            _goals_mod._PLANNING_SLOTS_CAPACITY = normalized_capacity
        semaphore = _goals_mod._PLANNING_SLOTS
    acquired = bool(semaphore.acquire(timeout=max(1, int(timeout_s))))
    return acquired, normalized_capacity


def _release_planning_slot() -> None:
    with _goals_mod._PLANNING_SLOTS_LOCK:
        semaphore = _goals_mod._PLANNING_SLOTS
    if semaphore is not None:
        semaphore.release()


def _set_planning_lease(goal_id: str, ttl_s: int = _PLANNING_LEASE_TTL_S) -> None:
    from sqlalchemy import text
    from agent.database import engine
    try:
        now = time.time()
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE goals
                    SET planning_lease_expires_at = :expires_at,
                        updated_at = :now
                    WHERE id = :goal_id
                      AND status = 'planning_running'
                    """
                ),
                {"expires_at": now + ttl_s, "now": now, "goal_id": str(goal_id)},
            )
    except Exception as exc:
        try:
            current_app.logger.exception("planning_lease_set_failed goal_id=%s error=%s", goal_id, exc)
        except Exception:
            pass


def _clear_planning_lease(goal_id: str) -> None:
    from sqlalchemy import text
    from agent.database import engine
    try:
        now = time.time()
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE goals
                    SET planning_lease_expires_at = NULL,
                        updated_at = :now
                    WHERE id = :goal_id
                    """
                ),
                {"now": now, "goal_id": str(goal_id)},
            )
    except Exception as exc:
        try:
            current_app.logger.exception("planning_lease_clear_failed goal_id=%s error=%s", goal_id, exc)
        except Exception:
            pass


def _start_planning_heartbeat(*, goal_id: str, stop_event: threading.Event, interval_s: int = _PLANNING_LEASE_TTL_S // 2) -> threading.Thread:
    def _beat() -> None:
        while not stop_event.wait(timeout=max(5, interval_s)):
            _set_planning_lease(goal_id, ttl_s=_PLANNING_LEASE_TTL_S)

    thread = threading.Thread(target=_beat, daemon=True, name=f"planning-heartbeat-{goal_id[:8]}")
    thread.start()
    return thread


def _start_planning_deadline_guard(*, goal_id: str, app: Any, timeout_s: int) -> None:
    from agent.services.planning_timeout_service import get_planning_timeout_service
    get_planning_timeout_service().start_deadline_guard(
        goal_id=goal_id,
        timeout_s=timeout_s,
        trace_id=None,
        app=app,
    )


def _run_goal_planning_background(*, goal_id: str, context: dict[str, Any], app: Any) -> None:
    with app.app_context():
        _run_goal_planning_background_impl(goal_id=goal_id, context=context)


def _run_goal_planning_background_impl(*, goal_id: str, context: dict[str, Any]) -> None:
    from agent.routes.tasks.goals import _plan_quality_from_task_ids
    from agent.routes.tasks.goals_helpers import (
        _cancel_stale_planning_goals,
        _is_soft_planning_quality_failure,
        _looks_like_software_goal,
        _mark_started_planning_runs_failed,
        _services,
    )
    from agent.services.planning_singleflight_service import get_planning_singleflight_service
    from agent.services.goal_config_runtime_service import get_goal_config_runtime_service
    from agent.services.product_event_service import record_product_event

    planning_policy = (current_app.config.get("AGENT_CONFIG") or {}).get("planning_policy") or {}
    singleflight_ttl = int((planning_policy.get("singleflight_ttl_seconds") or 900))
    if not get_planning_singleflight_service().acquire(goal_id=goal_id, ttl_seconds=singleflight_ttl):
        try:
            current_app.logger.warning("goal_planning_skip_duplicate_inflight goal_id=%s", goal_id)
        except Exception:
            pass
        return
    with _GOAL_ACTIVE_PLANNING_LOCK:
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
        planning_execute_timeout_s = int(max(30, _pp_timeout if _pp_timeout is not None else 300))
        planning_queue_wait_timeout_s = int(max(10, _pp_queue_wait if _pp_queue_wait is not None else planning_execute_timeout_s))
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
            _result_holder: dict[str, Any] = {}
            _error_holder: dict[str, BaseException] = {}
            _done = threading.Event()

            def _planning_call_runner() -> None:
                try:
                    _result_holder["result"] = _run_plan_goal_with_app_context()
                except Exception as exc:
                    _error_holder["error"] = exc
                finally:
                    _done.set()

            planning_call_thread = threading.Thread(
                target=_planning_call_runner,
                daemon=True,
                name=f"goal-planning-call-{goal_record.id[:8]}",
            )
            planning_call_thread.start()
            try:
                finished = _done.wait(timeout=outer_planning_timeout_s)
                if not finished:
                    raise FutureTimeoutError()
                if "error" in _error_holder:
                    raise _error_holder["error"]
                result = _result_holder.get("result")
            finally:
                _heartbeat_stop.set()
                _clear_planning_lease(goal_record.id)
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
                planning_policy=_resolved_pp,
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
            quality_parts = [part.strip() for part in str(quality_reason or "").split("|") if part.strip()]
            missing_task_kinds: list[str] = []
            for part in quality_parts:
                if part.startswith("missing_task_kinds:"):
                    missing_task_kinds = [item.strip() for item in part.split(":", 1)[1].split(",") if item.strip()]
            if missing_task_kinds:
                retry_mode_data = dict(goal_record.mode_data or {})
                retry_mode_data["planning_repair_context"] = {
                    "missing_task_kinds": missing_task_kinds,
                    "error_codes": [part for part in quality_parts if ":" not in part],
                }
                retry_result = auto_planner.plan_goal(
                    goal=str(context.get("goal_text") or goal_record.goal or ""),
                    context=context.get("mode_context"),
                    team_id=effective.get("routing", {}).get("team_id"),
                    create_tasks=True,
                    use_template=bool(effective.get("planning", {}).get("use_template", True)),
                    use_repo_context=bool(effective.get("planning", {}).get("use_repo_context", True)),
                    goal_id=goal_record.id,
                    goal_trace_id=goal_record.trace_id,
                    mode="new_software_project",
                    mode_data=retry_mode_data,
                )
                retry_task_ids = list(retry_result.get("created_task_ids") or [])
                if not retry_result.get("error") and retry_task_ids:
                    retry_quality_ok, retry_quality_reason = _plan_quality_from_task_ids(
                        task_ids=retry_task_ids,
                        mode="new_software_project",
                        planning_policy=_resolved_pp,
                        team_id=str(effective.get("routing", {}).get("team_id") or "") or None,
                    )
                    if retry_quality_ok or _is_soft_planning_quality_failure(quality_reason=retry_quality_reason):
                        result = retry_result
                        created_task_ids = retry_task_ids
                        quality_ok = retry_quality_ok
                        quality_reason = retry_quality_reason

        if create_tasks_enabled and software_goal and not quality_ok and not _is_soft_planning_quality_failure(quality_reason=quality_reason):
            quality_parts = [part.strip() for part in str(quality_reason or "").split("|") if part.strip()]
            error_codes = [part for part in quality_parts if ":" not in part]
            missing_task_kinds: list[str] = []
            for part in quality_parts:
                if part.startswith("missing_task_kinds:"):
                    missing_task_kinds = [item.strip() for item in part.split(":", 1)[1].split(",") if item.strip()]
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
                    "error_codes": error_codes,
                    "missing_task_kinds": missing_task_kinds,
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
        get_planning_singleflight_service().release(goal_id=goal_id)
        with _GOAL_ACTIVE_PLANNING_LOCK:
            _GOAL_ACTIVE_PLANNING_IDS.discard(goal_id)
