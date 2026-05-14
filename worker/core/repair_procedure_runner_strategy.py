from __future__ import annotations

from worker.core.propose_orchestrator import ProposeContext, ProposeStrategy
from worker.core.propose import ProposeStrategyResult, ExecutableProposal


class RepairProcedureRunnerStrategy(ProposeStrategy):
    """Generate repair action proposals from critique payloads."""

    def run(self, context: ProposeContext) -> ProposeStrategyResult:
        task = context.task or {}
        critique = dict(task.get("verification_critique") or {})
        if not critique:
            return ProposeStrategyResult.declined("repair_procedure_runner", reason="missing_verification_critique")
        missing_paths = [str(item).strip() for item in list(critique.get("missing_paths") or []) if str(item).strip()]
        if missing_paths:
            message = "\n".join(f"- {path}" for path in missing_paths)
            proposal = ExecutableProposal(
                proposal_id=f"repair-{context.task_id}",
                goal_id=context.goal_id,
                task_id=context.task_id,
                strategy_id="repair_procedure_runner",
                command=None,
                tool_calls=[
                    {
                        "name": "file_write",
                        "args": {
                            "path": "REPAIR_PLAN.md",
                            "content": "# Repair plan\n\nMissing artifacts:\n" + message + "\n",
                        },
                    }
                ],
                expected_artifacts=[{"kind": "file", "required": True, "relative_path": "REPAIR_PLAN.md"}],
                metadata={"source": "verification_critique", "missing_paths_count": len(missing_paths)},
            )
            return ProposeStrategyResult.executable("repair_procedure_runner", proposal)
        return ProposeStrategyResult.needs_review(
            "repair_procedure_runner",
            "repair_requested_without_missing_paths",
            metadata={"source": "verification_critique"},
        )

