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
        with _lock:
            callback = _planner_callback
        if callback is None:
            return {"error": "planner_callback_unavailable"}
        return callback(**kwargs)


_service = GoalPlanningRecoveryService()


def get_goal_planning_recovery_service() -> GoalPlanningRecoveryService:
    return _service
