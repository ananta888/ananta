"""Chain of Responsibility for heuristic rule evaluation.

Chain elements are evaluated in priority order (lowest number first).
The first element that returns status='handled' short-circuits the chain.
Elements returning 'abstain' are skipped; 'continue' passes to the next element.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any

from agent.services.heuristic_runtime.decision_context import DecisionContext
from agent.services.heuristic_runtime.decision_result import DecisionResult


@dataclass
class ChainResult:
    status: str  # handled | continue | abstain
    result: DecisionResult | None = None
    reason: str = ""
    rule_id: str = ""

    @staticmethod
    def handled(result: DecisionResult, *, rule_id: str = "", reason: str = "") -> "ChainResult":
        return ChainResult(status="handled", result=result, rule_id=rule_id, reason=reason)

    @staticmethod
    def continue_(*, rule_id: str = "", reason: str = "") -> "ChainResult":
        return ChainResult(status="continue", rule_id=rule_id, reason=reason)

    @staticmethod
    def abstain(*, rule_id: str = "", reason: str = "") -> "ChainResult":
        return ChainResult(status="abstain", rule_id=rule_id, reason=reason)


class HeuristicRuleChainElement(abc.ABC):
    """Abstract chain element. Subclasses declare a priority and implement handle()."""

    priority: int = 50

    @property
    def rule_id(self) -> str:
        return self.__class__.__name__

    @abc.abstractmethod
    def handle(
        self,
        ctx: DecisionContext,
        result: DecisionResult | None,
    ) -> ChainResult: ...


class RuleChain:
    """Executes a sorted list of HeuristicRuleChainElements."""

    def __init__(self, elements: list[HeuristicRuleChainElement]) -> None:
        self._elements = sorted(elements, key=lambda e: e.priority)

    def run(self, ctx: DecisionContext) -> DecisionResult:
        """Run the chain. Returns the first 'handled' result, or no_good_match."""
        current: DecisionResult | None = None
        for element in self._elements:
            cr = element.handle(ctx, current)
            if cr.status == "abstain":
                continue
            if cr.status == "handled" and cr.result is not None:
                return cr.result
            if cr.status == "continue" and cr.result is not None:
                current = cr.result
        return DecisionResult.no_good_match()

    def add(self, element: HeuristicRuleChainElement) -> None:
        self._elements = sorted(self._elements + [element], key=lambda e: e.priority)

    @property
    def elements(self) -> list[HeuristicRuleChainElement]:
        return list(self._elements)
