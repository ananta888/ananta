"""AdvisoryProposalStrategy — stores task prompt as advisory; penultimate fallback."""
from __future__ import annotations

from worker.core.propose_orchestrator import ProposeContext, ProposeStrategy
from worker.core.propose import ProposeStrategyResult


class AdvisoryProposalStrategy(ProposeStrategy):
    """Returns an advisory result with the task prompt for human review."""

    def run(self, context: ProposeContext) -> ProposeStrategyResult:
        advisory_text = (
            f"Advisory proposal for task {context.task_id}:\n\n"
            f"{context.base_prompt}"
        )
        return ProposeStrategyResult.advisory(
            "advisory_proposal",
            advisory_text=advisory_text,
            metadata={"source": "advisory_proposal_strategy", "task_id": context.task_id},
        )
