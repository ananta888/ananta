from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

from agent.planning_reason_codes import PLANNING_DEADLINE_GUARD_TIMEOUT
from agent.services.repository_registry import get_repository_registry
from agent.services.service_registry import get_core_services
from agent.services.product_event_service import record_product_event
from agent.services.planning_telemetry_service import get_planning_telemetry_service


@dataclass(frozen=True)
class _GuardToken:
    goal_id: str
    started_at: float


class PlanningTimeoutService:
    """Centralized planning deadline guard with per-goal singleflight semantics."""

    def __init__(self) -> None:
        self._active: dict[str, _GuardToken] = {}
        self._lock = threading.Lock()

    def _acquire(self, goal_id: str) -> bool:
        with self._lock:
            if goal_id in self._active:
                return False
            self._active[goal_id] = _GuardToken(goal_id=goal_id, started_at=time.time())
            return True

    def _release(self, goal_id: str) -> None:
        with self._lock:
            self._active.pop(goal_id, None)

    def _run_once(self, *, goal_id: str, timeout_s: int, app: Any, sleep_fn: Callable[[float], None]) -> None:
        sleep_fn(max(1, int(timeout_s)))
        with app.app_context():
            repos = get_repository_registry()
            services = get_core_services()
            goal = repos.goal_repo.get_by_id(goal_id)
            if not goal:
                return
            status = str(getattr(goal, "status", "") or "").strip().lower()
            if status != "planning_running":
                return
            runs = list(repos.planning_run_repo.get_by_goal_id(goal_id, limit=50) or [])
            for run in runs:
                if str(getattr(run, "goal_id", "") or "").strip() != goal_id:
                    continue
                if str(getattr(run, "status", "") or "").strip().lower() != "started":
                    continue
                get_planning_telemetry_service().update_run(
                    run,
                    status="failed",
                    error_classification=PLANNING_DEADLINE_GUARD_TIMEOUT,
                    validation_errors=[PLANNING_DEADLINE_GUARD_TIMEOUT],
                )
            services.goal_lifecycle_service.transition_goal(
                goal,
                target_status="failed",
                reason=PLANNING_DEADLINE_GUARD_TIMEOUT,
                readiness=dict(getattr(goal, "readiness", None) or {}),
            )
            record_product_event(
                "goal_planning_failed",
                actor="auto_planner",
                details={"reason": PLANNING_DEADLINE_GUARD_TIMEOUT, "timeout_seconds": int(timeout_s)},
                goal_id=goal_id,
                trace_id=str(getattr(goal, "trace_id", "") or ""),
                plan_id=None,
            )

    def start_deadline_guard(self, *, goal_id: str, timeout_s: int, trace_id: str | None = None, app: Any | None = None) -> bool:
        app_obj = app
        if app_obj is None:
            from flask import current_app

            app_obj = current_app._get_current_object()
        if bool(getattr(app_obj, "testing", False) or (getattr(app_obj, "config", {}) or {}).get("TESTING")):
            planning_policy = ((getattr(app_obj, "config", {}) or {}).get("AGENT_CONFIG") or {}).get("planning_policy") or {}
            if not bool(planning_policy.get("enable_deadline_guard_threads_in_tests", False)):
                return False
        if not self._acquire(goal_id):
            return False

        def _runner() -> None:
            try:
                self._run_once(goal_id=goal_id, timeout_s=timeout_s, app=app_obj, sleep_fn=time.sleep)
            finally:
                self._release(goal_id)

        thread = threading.Thread(target=_runner, daemon=True, name=f"planning-timeout-{goal_id[:8]}")
        thread.start()
        return True


_SERVICE = PlanningTimeoutService()


def get_planning_timeout_service() -> PlanningTimeoutService:
    return _SERVICE
