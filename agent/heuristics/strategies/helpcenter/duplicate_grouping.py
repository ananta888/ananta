"""DuplicateFailureGroupingStrategy — helpcenter_duplicate_failure_grouping_default."""
from __future__ import annotations

from agent.heuristics.strategies.base import ArtifactRefPort, CodeCompassReadPort, HeuristicStrategyBase
from agent.heuristics.strategies.scoring import build_reason_codes, keyword_score
from agent.services.heuristic_runtime.decision_context import DecisionContext
from agent.services.heuristic_runtime.decision_result import DecisionResult
from agent.services.heuristic_runtime.heuristic_registry_service import HeuristicDefinition

_DUPLICATE_KEYWORDS = [
    "duplicate", "same", "similar", "again", "already", "recur",
    "repeated", "flaky", "intermittent", "known issue",
]
_MIN_SCORE = 0.1


class DuplicateFailureGroupingStrategy(HeuristicStrategyBase):
    """Group similar failures and surface existing known-issue refs.

    Activates when the query suggests a recurring or duplicate failure
    pattern. Looks up existing helpcenter refs to group against.
    Never creates new groupings autonomously. Deterministic.
    """

    def domain(self) -> str:
        return "helpcenter"

    def evaluate(
        self,
        context: DecisionContext,
        definition: HeuristicDefinition,
    ) -> DecisionResult:
        cc = CodeCompassReadPort.from_context(context)
        art = ArtifactRefPort.from_context(context)
        params = definition.parameters or {}
        query = str(context.query or "")
        min_score = float(params.get("min_score", _MIN_SCORE))

        score = keyword_score(query, _DUPLICATE_KEYWORDS)
        has_refs = bool(cc.allowed_source_scopes or art.selected_artifacts)

        if score < min_score:
            return DecisionResult(
                action_kind="no_action",
                confidence=1.0,
                source="heuristic",
                strategy_id=self.strategy_id,
                reason_codes=["duplicate_grouping:no_duplicate_keywords"],
            )

        if not has_refs:
            return DecisionResult(
                action_kind="ask_scope",
                confidence=0.6,
                source="heuristic",
                strategy_id=self.strategy_id,
                reason_codes=["duplicate_grouping:no_refs"],
            )

        codes = build_reason_codes(
            f"duplicate_grouping:score={score:.2f}",
            f"scopes:{len(cc.allowed_source_scopes)}",
        )
        return DecisionResult(
            action_kind="show_context_summary",
            confidence=min(0.6 + score * 0.35, 0.88),
            source="heuristic",
            strategy_id=self.strategy_id,
            reason_codes=codes,
        )
