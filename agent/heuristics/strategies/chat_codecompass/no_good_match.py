"""NoGoodMatchStrategy — chat_codecompass_no_good_match_default.

Anti-hallucination terminal fallback: when no other strategy matches,
return no_action. Never fabricates an answer. Confidence=1.0 (always correct
to do nothing than to hallucinate). Deterministic.
"""
from __future__ import annotations

from agent.heuristics.strategies.base import HeuristicStrategyBase
from agent.services.heuristic_runtime.decision_context import DecisionContext
from agent.services.heuristic_runtime.decision_result import DecisionResult
from agent.services.heuristic_runtime.heuristic_registry_service import HeuristicDefinition


class NoGoodMatchStrategy(HeuristicStrategyBase):
    """Terminal no-action fallback — anti-hallucination guard.

    Returns no_action with confidence=1.0. Intended as the last strategy
    in a composite chain. Ensures the snake never acts on insufficient context.
    """

    def domain(self) -> str:
        return "chat_codecompass"

    def evaluate(
        self,
        context: DecisionContext,
        definition: HeuristicDefinition,
    ) -> DecisionResult:
        return DecisionResult(
            action_kind="no_action",
            confidence=1.0,
            source="heuristic",
            strategy_id=self.strategy_id,
            reason_codes=["no_good_match:anti_hallucination_guard"],
        )
