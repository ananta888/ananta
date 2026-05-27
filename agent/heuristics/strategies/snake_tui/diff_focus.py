"""TuiDiffFocusStrategy — implements snake_tui_diff_focus_default."""
from __future__ import annotations

from agent.heuristics.strategies.base import HeuristicStrategyBase, UiMotionPort
from agent.services.heuristic_runtime.decision_context import DecisionContext
from agent.services.heuristic_runtime.decision_result import DecisionResult
from agent.services.heuristic_runtime.heuristic_registry_service import HeuristicDefinition

_DIFF_PANEL_KEYWORDS = ("diff", "compare", "git", "merge")
_DIFF_EVENTS = frozenset({"diff_opened", "compare_opened", "hunk_focused", "conflict_focused"})


class TuiDiffFocusStrategy(HeuristicStrategyBase):
    """Lurk near active diff hunk when a diff/compare panel is open.

    Detects diff panels by panel name keywords or recent diff events.
    Falls back to follow when not in a diff context. Deterministic: no AI calls.
    """

    def domain(self) -> str:
        return "tui_snake"

    def evaluate(
        self,
        context: DecisionContext,
        definition: HeuristicDefinition,
    ) -> DecisionResult:
        ui = UiMotionPort.from_context(context)
        panel = (ui.active_panel or "").lower()

        in_diff_panel = any(kw in panel for kw in _DIFF_PANEL_KEYWORDS)
        has_diff_event = bool(_DIFF_EVENTS & set(ui.recent_event_types))

        if not in_diff_panel and not has_diff_event:
            return DecisionResult.fallback(
                reason="not_in_diff_context",
                strategy_id=self.strategy_id,
            )

        zone = ui.active_panel or "diff"
        return DecisionResult(
            action_kind="lurk",
            confidence=0.95,
            source="heuristic",
            strategy_id=self.strategy_id,
            reason_codes=[f"diff_focus:zone={zone}"],
        )
