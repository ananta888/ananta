from __future__ import annotations

import threading
from typing import Any, Callable

_lock = threading.Lock()
_planner_callback: Callable[..., dict[str, Any]] | None = None


def register_recovery_planner_callback(callback: Callable[..., dict[str, Any]]) -> None:
    global _planner_callback
    with _lock:
        _planner_callback = callback


class GoalPlanningRecoveryService:
    def plan_goal(self, **kwargs: Any) -> dict[str, Any]:
        try:
            from agent.routes.tasks import auto_planner as auto_planner_module

            planner = getattr(auto_planner_module, "auto_planner", None)
            plan_goal = getattr(planner, "plan_goal", None)
            if callable(plan_goal):
                return plan_goal(**kwargs)
        except Exception:
            pass
        with _lock:
            callback = _planner_callback
        if callback is None:
            return {"error": "planner_callback_unavailable"}
        return callback(**kwargs)


_service = GoalPlanningRecoveryService()


def get_goal_planning_recovery_service() -> GoalPlanningRecoveryService:
    return _service
