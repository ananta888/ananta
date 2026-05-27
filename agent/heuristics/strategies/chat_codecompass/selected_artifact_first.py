"""SelectedArtifactFirstStrategy — chat_codecompass_selected_artifact_first."""
from __future__ import annotations

from agent.heuristics.strategies.base import ArtifactRefPort, CodeCompassReadPort, HeuristicStrategyBase
from agent.heuristics.strategies.scoring import build_reason_codes
from agent.services.heuristic_runtime.decision_context import DecisionContext
from agent.services.heuristic_runtime.decision_result import DecisionResult
from agent.services.heuristic_runtime.heuristic_registry_service import HeuristicDefinition


class SelectedArtifactFirstStrategy(HeuristicStrategyBase):
    """Prioritise selected artifact as the first source reference in chat.

    When one or more artifacts are selected/highlighted, opens the first one
    as the primary source ref for the chat response. Anti-hallucination:
    returns no_action when no artifact is selected. Deterministic.
    """

    def domain(self) -> str:
        return "chat_codecompass"

    def evaluate(
        self,
        context: DecisionContext,
        definition: HeuristicDefinition,
    ) -> DecisionResult:
        art = ArtifactRefPort.from_context(context)
        cc = CodeCompassReadPort.from_context(context)

        if not art.selected_artifacts:
            return DecisionResult(
                action_kind="no_action",
                confidence=1.0,
                source="heuristic",
                strategy_id=self.strategy_id,
                reason_codes=["selected_artifact_first:no_artifact"],
            )

        primary = art.selected_artifacts[0]
        codes = build_reason_codes(
            f"selected_artifact:{primary[:48]}",
            f"scope_count:{len(cc.allowed_source_scopes)}",
        )

        return DecisionResult(
            action_kind="open_source_ref",
            confidence=0.95,
            source="heuristic",
            strategy_id=self.strategy_id,
            reason_codes=codes,
        )
