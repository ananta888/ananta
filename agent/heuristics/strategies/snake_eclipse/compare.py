"""EclipseCompareStrategy — snake_eclipse_compare_default."""
from __future__ import annotations

from agent.heuristics.strategies.base import HeuristicStrategyBase, UiMotionPort
from agent.services.heuristic_runtime.decision_context import DecisionContext
from agent.services.heuristic_runtime.decision_result import DecisionResult, SuggestedMotion
from agent.services.heuristic_runtime.heuristic_registry_service import HeuristicDefinition

_COMPARE_KEYWORDS = ("compare", "diff", "git_compare", "merge", "conflict")


class EclipseCompareStrategy(HeuristicStrategyBase):
    """Follow diff direction in Eclipse Compare/Git compare view.

    When a diff or compare panel is active, follows the natural left→right
    reading direction of diffs. Configurable side preference. Deterministic.
    """

    def domain(self) -> str:
        return "snake_eclipse"

    def evaluate(
        self,
        context: DecisionContext,
        definition: HeuristicDefinition,
    ) -> DecisionResult:
        ui = UiMotionPort.from_context(context)
        panel = (ui.active_panel or "").lower()
        params = definition.parameters or {}

        in_compare = any(kw in panel for kw in _COMPARE_KEYWORDS)

        if not in_compare:
            return DecisionResult.fallback(
                reason="not_in_compare_zone",
                strategy_id=self.strategy_id,
            )

        # Default: follow left→right (dx=1) for diff reading direction
        # "right_side" param lets operator prefer the new-file side
        right_side = bool(params.get("right_side", True))
        dx = 1 if right_side else -1

        return DecisionResult(
            action_kind="follow",
            confidence=0.88,
            source="heuristic",
            suggested_motion=SuggestedMotion(dx=dx, dy=0),
            strategy_id=self.strategy_id,
            reason_codes=[f"eclipse_compare:side={'right' if right_side else 'left'}"],
        )
