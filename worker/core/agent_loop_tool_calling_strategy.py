"""WSM-T002: bounded agent-loop tool calling strategy."""
from __future__ import annotations

from worker.core.propose_orchestrator import ProposeContext, ProposeStrategy
from worker.core.propose import ProposeStrategyResult
from worker.core.tool_calling_llm_strategy import ToolCallingLLMStrategy


class AgentLoopToolCallingStrategy(ProposeStrategy):
    """A bounded wrapper around tool-calling for iterative-agent style modes.

    Current implementation intentionally keeps one bounded iteration to avoid
    uncontrolled loops; orchestration can call this strategy again in later cycles.
    """

    def run(self, context: ProposeContext) -> ProposeStrategyResult:
        raw_iterations = (context.task or {}).get("max_agent_loop_iterations")
        max_iterations = int(1 if raw_iterations is None else raw_iterations)
        if max_iterations < 1:
            return ProposeStrategyResult.needs_review(
                "agent_loop_tool_calling",
                "agent_loop_iterations_disabled",
                reason_codes=["agent_loop_disabled"],
            )

        base = ToolCallingLLMStrategy().run(context)
        if isinstance(base.metadata, dict):
            base.metadata.setdefault("agent_loop", {})
            base.metadata["agent_loop"]["iteration"] = 1
            base.metadata["agent_loop"]["max_iterations"] = max_iterations
        return base
