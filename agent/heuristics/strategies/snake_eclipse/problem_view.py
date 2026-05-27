"""EclipseProblemViewStrategy — snake_eclipse_problem_view_default."""
from __future__ import annotations

from agent.heuristics.strategies.base import ArtifactRefPort, HeuristicStrategyBase, UiMotionPort
from agent.services.heuristic_runtime.decision_context import DecisionContext
from agent.services.heuristic_runtime.decision_result import DecisionResult
from agent.services.heuristic_runtime.heuristic_registry_service import HeuristicDefinition

_PROBLEMS_KEYWORDS = ("problems", "error", "warning", "markers", "issues")


class EclipseProblemViewStrategy(HeuristicStrategyBase):
    """Lurk near problem/error context in Eclipse Problems view.

    Activates when the Problems view or error markers are the active zone.
    Encourages the snake to stay near error context rather than chasing
    cursor movement. Deterministic: no AI calls.
    """

    def domain(self) -> str:
        return "snake_eclipse"

    def evaluate(
        self,
        context: DecisionContext,
        definition: HeuristicDefinition,
    ) -> DecisionResult:
        ui = UiMotionPort.from_context(context)
        art = ArtifactRefPort.from_context(context)
        panel = (ui.active_panel or "").lower()

        in_problems = any(kw in panel for kw in _PROBLEMS_KEYWORDS)

        if not in_problems:
            return DecisionResult.fallback(
                reason="not_in_problems_zone",
                strategy_id=self.strategy_id,
            )

        zone_label = ui.active_panel or "problems"
        artifact_hint = art.selected_artifacts[0][:24] if art.selected_artifacts else ""

        codes = [f"eclipse_problems:{zone_label}"]
        if artifact_hint:
            codes.append(f"artifact:{artifact_hint}")

        return DecisionResult(
            action_kind="lurk",
            confidence=0.9,
            source="heuristic",
            strategy_id=self.strategy_id,
            reason_codes=codes,
        )
