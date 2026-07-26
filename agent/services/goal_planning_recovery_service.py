from __future__ import annotations

import threading
from typing import Any, Callable

_lock = threading.Lock()
_planner_callback: Callable[..., dict[str, Any]] | None = None
_planner_stats_provider: Callable[[], dict[str, Any]] | None = None
_fallback_stats: dict[str, Any] = {"tasks_created": 0}


def register_recovery_planner_callback(
    callback: Callable[..., dict[str, Any]],
    *,
    stats_provider: Callable[[], dict[str, Any]] | None = None,
) -> None:
    global _planner_callback, _planner_stats_provider
    with _lock:
        _planner_callback = callback
        _planner_stats_provider = stats_provider


class GoalPlanningRecoveryService:
    @property
    def _stats(self) -> dict[str, Any]:
        """Expose the composed planner's mutable metrics to PlanningService."""

        with _lock:
            provider = _planner_stats_provider
        if provider is None:
            return _fallback_stats
        stats = provider()
        return stats if isinstance(stats, dict) else _fallback_stats

    def plan_goal(self, **kwargs: Any) -> dict[str, Any]:
        with _lock:
            callback = _planner_callback
        if callback is None:
            return {"error": "planner_callback_unavailable"}
        return callback(**kwargs)


_service = GoalPlanningRecoveryService()


def get_goal_planning_recovery_service() -> GoalPlanningRecoveryService:
    return _service
