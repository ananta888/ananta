"""TuiLurkFocusStrategy — implements snake_tui_lurk_focus_default."""
from __future__ import annotations

from agent.heuristics.strategies.base import HeuristicStrategyBase, UiMotionPort
from agent.services.heuristic_runtime.decision_context import DecisionContext
from agent.services.heuristic_runtime.decision_result import DecisionResult
from agent.services.heuristic_runtime.heuristic_registry_service import HeuristicDefinition

_DEFAULT_IDLE_THRESHOLD = 3.0  # seconds
_POINTER_IDLE_EVENT = "pointer_idle"


class TuiLurkFocusStrategy(HeuristicStrategyBase):
    """Lurk near stable focus position.

    Activates when pointer/focus is stable (idle event present) or AI is offline.
    Deterministic: no AI calls.
    """

    def domain(self) -> str:
        return "tui_snake"

    def evaluate(
        self,
        context: DecisionContext,
        definition: HeuristicDefinition,
    ) -> DecisionResult:
        ui = UiMotionPort.from_context(context)
        params = definition.parameters or {}
        idle_threshold = float(params.get("idle_threshold_seconds") or _DEFAULT_IDLE_THRESHOLD)

        is_idle = (
            _POINTER_IDLE_EVENT in ui.recent_event_types
            or context.ai_status in ("offline", "timeout")
        )

        if not is_idle:
            # Fall back to follow if not in idle state
            return DecisionResult.fallback(
                reason="not_idle",
                strategy_id=self.strategy_id,
            )

        zone = str(context.active_panel or context.active_goal_id or "")
        return DecisionResult(
            action_kind="lurk",
            confidence=1.0,
            source="heuristic",
            strategy_id=self.strategy_id,
            reason_codes=[f"lurk_focus:zone={zone}"],
        )
