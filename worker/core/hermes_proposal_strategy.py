"""WSM-T004: Hermes-like proposal/review-first strategy."""
from __future__ import annotations

from worker.core.propose_orchestrator import ProposeContext, ProposeStrategy
from worker.core.propose import ProposeStrategyResult, PlannerProposalArtifact


class HermesProposalStrategy(ProposeStrategy):
    """Non-mutating proposal strategy. Never returns executable output."""

    def run(self, context: ProposeContext) -> ProposeStrategyResult:
        task_desc = str((context.task or {}).get("description") or context.base_prompt or "").strip()
        if not task_desc:
            return ProposeStrategyResult.declined(
                "hermes_proposal_strategy",
                reason="missing_task_description",
            )
        artifact = PlannerProposalArtifact(
            proposal_id=f"hermes-plan-{context.task_id}",
            goal_id=context.goal_id,
            task_id=context.task_id,
            strategy_id="hermes_proposal_strategy",
            sub_tasks=[
                {
                    "task_id": f"{context.task_id}-proposal-1",
                    "title": "Review proposal",
                    "description": task_desc[:400],
                    "kind": "research",
                }
            ],
            metadata={"mode": "proposal_review_first", "mutating": False},
        )
        return ProposeStrategyResult.advisory(
            "hermes_proposal_strategy",
            advisory_text=task_desc[:400],
            advisory_artifact_ref=artifact.proposal_id,
            reason="hermes_proposal_generated",
            metadata={"mode": "proposal_review_first", "mutating": False},
        )
