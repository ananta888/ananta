"""WSM-T004: Hermes-like proposal/review-first strategy."""
from __future__ import annotations

from worker.core.propose_orchestrator import ProposeContext, ProposeStrategy
from worker.core.propose import ProposeStrategyResult, PlannerProposalArtifact


class HermesProposalStrategy(ProposeStrategy):
    """Non-mutating proposal strategy with adapter-aware advisory path."""

    def run(self, context: ProposeContext) -> ProposeStrategyResult:
        hermes_payload = self._extract_hermes_payload(context)
        if isinstance(hermes_payload, dict):
            summary = str(hermes_payload.get("summary") or "").strip()
            findings = hermes_payload.get("findings") if isinstance(hermes_payload.get("findings"), list) else []
            if summary or findings:
                advisory = summary or "; ".join(str(item) for item in findings[:3])
                return ProposeStrategyResult.advisory(
                    "hermes_proposal_strategy",
                    advisory_text=advisory[:700],
                    advisory_artifact_ref=str(hermes_payload.get("artifact_id") or f"hermes-plan-{context.task_id}"),
                    reason="hermes_adapter_proposal_used",
                    metadata={
                        "mode": "proposal_review_first",
                        "mutating": False,
                        "provider_path": "hermes_adapter",
                    },
                )

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

    @staticmethod
    def _extract_hermes_payload(context: ProposeContext) -> dict | None:
        rc = context.research_context if isinstance(context.research_context, dict) else {}
        for key in ("hermes", "hermes_result", "adapter_result"):
            candidate = rc.get(key)
            if isinstance(candidate, dict):
                return candidate
        return None
