"""WSM-T003: codex-cli-like patch proposal strategy."""
from __future__ import annotations

from worker.core.propose_orchestrator import ProposeContext, ProposeStrategy
from worker.core.propose import ProposeStrategyResult, PatchProposalArtifact


class CliAgentPatchStrategy(ProposeStrategy):
    """Produces non-executable patch proposals from diff-like input."""

    def run(self, context: ProposeContext) -> ProposeStrategyResult:
        raw = ""
        if isinstance(context.research_context, dict):
            raw = str(context.research_context.get("raw_output") or "")
        if not raw:
            raw = str(context.base_prompt or "")
        if "---" not in raw and "+++" not in raw and "@@" not in raw:
            return ProposeStrategyResult.declined(
                "cli_agent_patch_strategy",
                reason="no_patch_content_detected",
                reason_codes=["no_patch_content"],
            )

        artifact = PatchProposalArtifact(
            proposal_id=f"cli-patch-{context.task_id}",
            goal_id=context.goal_id,
            task_id=context.task_id,
            strategy_id="cli_agent_patch_strategy",
            patches=[{"path": "auto", "content": raw[:8000]}],
            metadata={"source_format": "unified_diff"},
        )
        return ProposeStrategyResult(
            status="advisory",
            strategy_id="cli_agent_patch_strategy",
            proposal=artifact,
            reason="patch_proposal_extracted",
            reason_codes=["patch_only_requires_apply_approval"],
            metadata={"source_format": "unified_diff"},
        )

