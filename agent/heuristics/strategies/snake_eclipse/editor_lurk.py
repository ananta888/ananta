"""EclipseEditorLurkStrategy — snake_eclipse_editor_lurk_default."""
from __future__ import annotations

from agent.heuristics.strategies.base import HeuristicStrategyBase, UiMotionPort
from agent.services.heuristic_runtime.decision_context import DecisionContext
from agent.services.heuristic_runtime.decision_result import DecisionResult, SuggestedMotion
from agent.services.heuristic_runtime.heuristic_registry_service import HeuristicDefinition

_EDITOR_KEYWORDS = ("editor", "source", "code_editor", "text_editor")


class EclipseEditorLurkStrategy(HeuristicStrategyBase):
    """Follow cursor in Eclipse editor zone.

    Active when the IDE zone is the main source editor. Uses cursor position
    heuristic to choose follow direction. Deterministic: no AI calls.
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

        in_editor = any(kw in panel for kw in _EDITOR_KEYWORDS) or panel == ""

        if not in_editor:
            return DecisionResult.fallback(
                reason="not_in_editor_zone",
                strategy_id=self.strategy_id,
            )

        # Default: follow cursor rightward in editor (most common editing direction)
        default_dx = int(params.get("default_dx", 1))
        default_dy = int(params.get("default_dy", 0))

        return DecisionResult(
            action_kind="follow",
            confidence=0.85,
            source="heuristic",
            suggested_motion=SuggestedMotion(dx=default_dx, dy=default_dy),
            strategy_id=self.strategy_id,
            reason_codes=["eclipse_editor_lurk"],
        )
