"""ProposeStrategyOrchestrator — central policy-driven proposal engine. FA-T005."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List

from worker.core.propose import (
    ProposeStrategyResult,
    ProposalBase,
    STATUS_ADVISORY,
    STATUS_FAILED,
    STATUS_NEEDS_REVIEW,
)

from agent.services.propose_policy import ProposePolicy


@dataclass
class ProposeContext:
    """Context for propose strategies."""
    goal_id: str
    task_id: str
    task: dict[str, Any]
    base_prompt: str
    research_context: dict[str, Any] | None = None
    cli_runner: 'Callable' = None  # Forwarded
    tool_definitions_resolver: 'Callable' = None  # Forwarded
    # Add more as needed


class ProposeStrategy(ABC):
    """Abstract propose strategy."""

    @abstractmethod
    def run(self, context: ProposeContext) -> ProposeStrategyResult:
        """Run strategy, return result."""
        ...


class ProposeStrategyOrchestrator:
    """Orchestrates strategies per policy."""

    def __init__(self, policy: ProposePolicy, strategies: Dict[str, ProposeStrategy]):
        self.policy = policy
        self.strategies = strategies

    def run(self, context: ProposeContext) -> ProposeStrategyResult:
        """Run strategies in policy order until executable or terminal."""
        strategy_order = self.policy.effective_strategy_order()
        for strategy_id in strategy_order[:self.policy.max_strategy_attempts]:
            strategy = self.strategies.get(strategy_id)
            if strategy is None:
                result = ProposeStrategyResult.declined(
                    strategy_id, reason="strategy_not_available"
                )
            else:
                result = strategy.run(context)
            if result.is_executable or result.is_terminal:
                return result
        # All declined
        match self.policy.on_all_strategies_declined:
            case "needs_review":
                return ProposeStrategyResult.needs_review(
                    "orchestrator", "all_strategies_declined_needs_review"
                )
            case "failed":
                return ProposeStrategyResult.failed(
                    "orchestrator", "all_strategies_declined_failed"
                )
            case "advisory":
                return ProposeStrategyResult.advisory(
                    "orchestrator",
                    advisory_text="All strategies declined; human review recommended.",
                )
            case _:
                raise ValueError(f"invalid_on_all_strategies_declined: {self.policy.on_all_strategies_declined}")

class StubStrategy(ProposeStrategy):
    def __init__(self, strategy_id: str):
        self.strategy_id = strategy_id

    def run(self, context: ProposeContext) -> ProposeStrategyResult:
        return ProposeStrategyResult.declined(self.strategy_id, "stub_not_implemented_yet")
