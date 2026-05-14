"""WSM-T002: bounded agent-loop tool calling strategy."""
from __future__ import annotations

from copy import deepcopy

from worker.core.propose_orchestrator import ProposeContext, ProposeStrategy
from worker.core.propose import ProposeStrategyResult
from worker.core.tool_calling_llm_strategy import ToolCallingLLMStrategy


class AgentLoopToolCallingStrategy(ProposeStrategy):
    """Bounded observe-act loop for tool-calling modes.

    The strategy does not execute tools locally. It iterates proposal attempts
    with compact loop-feedback context and stops after a bounded number of turns.
    """

    def run(self, context: ProposeContext) -> ProposeStrategyResult:
        raw_iterations = (context.task or {}).get("max_agent_loop_iterations")
        max_iterations = int(3 if raw_iterations is None else raw_iterations)
        if max_iterations < 1:
            return ProposeStrategyResult.needs_review(
                "agent_loop_tool_calling",
                "agent_loop_iterations_disabled",
                reason_codes=["agent_loop_disabled"],
            )

        attempted: list[dict] = []
        feedback: list[str] = []
        for iteration in range(1, max_iterations + 1):
            loop_context = self._loop_context(context, feedback=feedback, iteration=iteration, max_iterations=max_iterations)
            result = ToolCallingLLMStrategy().run(loop_context)
            attempted.append(
                {
                    "iteration": iteration,
                    "status": result.status,
                    "reason": result.reason,
                }
            )
            if isinstance(result.metadata, dict):
                result.metadata.setdefault("agent_loop", {})
                result.metadata["agent_loop"]["iteration"] = iteration
                result.metadata["agent_loop"]["max_iterations"] = max_iterations
                result.metadata["agent_loop"]["attempted_iterations"] = list(attempted)
            if result.is_executable or result.is_terminal:
                return result
            feedback.append(f"iteration={iteration} status={result.status} reason={result.reason}")

        return ProposeStrategyResult.needs_review(
            "agent_loop_tool_calling",
            "agent_loop_max_iterations_reached_without_executable",
            reason_codes=["agent_loop_max_iterations_reached"],
            metadata={
                "agent_loop": {
                    "max_iterations": max_iterations,
                    "attempted_iterations": attempted,
                }
            },
        )

    @staticmethod
    def _loop_context(context: ProposeContext, *, feedback: list[str], iteration: int, max_iterations: int) -> ProposeContext:
        task = deepcopy(context.task or {})
        loop_note = f"[agent_loop] iteration {iteration}/{max_iterations}"
        if feedback:
            loop_note += "\nPrevious outcomes:\n- " + "\n- ".join(feedback[-3:])
        prompt = f"{context.base_prompt}\n\n{loop_note}".strip()
        return ProposeContext(
            goal_id=context.goal_id,
            task_id=context.task_id,
            task=task,
            base_prompt=prompt,
            research_context=deepcopy(context.research_context) if isinstance(context.research_context, dict) else context.research_context,
            cli_runner=context.cli_runner,
            tool_definitions_resolver=context.tool_definitions_resolver,
            policy=context.policy,
        )
