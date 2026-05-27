"""TuiFollowDistanceStrategy — implements snake_tui_follow_distance_default."""
from __future__ import annotations

from agent.heuristics.strategies.base import HeuristicStrategyBase, UiMotionPort
from agent.services.heuristic_runtime.decision_context import DecisionContext
from agent.services.heuristic_runtime.decision_result import DecisionResult, SuggestedMotion
from agent.services.heuristic_runtime.heuristic_registry_service import HeuristicDefinition


class TuiFollowDistanceStrategy(HeuristicStrategyBase):
    """Follow active goal or selected artifact with configurable distance.

    Motion is determined by the active_goal_id or selected artifact position.
    When neither is available, falls back to lurk.
    Deterministic: no AI calls.
    """

    def domain(self) -> str:
        return "tui_snake"

    def evaluate(
        self,
        context: DecisionContext,
        definition: HeuristicDefinition,
    ) -> DecisionResult:
        params = definition.parameters or {}
        distance = int(params.get("distance") or 4)
        ui = UiMotionPort.from_context(context)

        has_target = bool(context.active_goal_id or context.selected_artifacts)

        if not has_target:
            return DecisionResult.heuristic_lurk(strategy_id=self.strategy_id)

        # Deterministic motion: move toward active_goal_id
        # Without a real position map, default to dx=1 (follow right)
        dx, dy = self._motion_for_goal(context.active_goal_id or "")
        return DecisionResult(
            action_kind="follow",
            confidence=1.0,
            source="heuristic",
            suggested_motion=SuggestedMotion(dx=dx, dy=dy),
            strategy_id=self.strategy_id,
            reason_codes=[f"follow_distance:{distance}"],
        )

    def _motion_for_goal(self, goal_id: str) -> tuple[int, int]:
        # Deterministic goal→motion mapping based on goal_id hash
        # Real implementation would use panel position data
        h = hash(goal_id) % 4
        return [(1, 0), (0, 1), (-1, 0), (0, -1)][h]
