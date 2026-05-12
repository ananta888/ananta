"""HumanReviewStrategy — terminal escalation; all prior strategies declined."""
from __future__ import annotations

from worker.core.propose_orchestrator import ProposeContext, ProposeStrategy
from worker.core.propose import ProposeStrategyResult


class HumanReviewStrategy(ProposeStrategy):
    """Terminal strategy. Returns needs_review — cannot be executed."""

    def run(self, context: ProposeContext) -> ProposeStrategyResult:
        return ProposeStrategyResult.needs_review(
            "human_review",
            "all_prior_strategies_declined_human_review_required",
            metadata={"task_id": context.task_id, "goal_id": context.goal_id},
        )
