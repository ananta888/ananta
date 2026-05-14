from unittest.mock import Mock

from worker.core.agent_loop_tool_calling_strategy import AgentLoopToolCallingStrategy
from worker.core.propose_orchestrator import ProposeContext
from worker.core.propose import STATUS_NEEDS_REVIEW, STATUS_EXECUTABLE


def test_agent_loop_disabled_when_max_iterations_zero():
    ctx = ProposeContext(goal_id="g", task_id="t", task={"max_agent_loop_iterations": 0}, base_prompt="x")
    result = AgentLoopToolCallingStrategy().run(ctx)
    assert result.status == STATUS_NEEDS_REVIEW


def test_agent_loop_wraps_tool_calling(monkeypatch):
    from worker.core.tool_calling_llm_strategy import ToolCallingLLMStrategy
    from worker.core.propose import ExecutableProposal, ProposeStrategyResult

    def _run(_self, context):
        p = ExecutableProposal.from_command(goal_id=context.goal_id, task_id=context.task_id, strategy_id="tool_calling_llm", command="echo ok")
        return ProposeStrategyResult.executable("tool_calling_llm", p)

    monkeypatch.setattr(ToolCallingLLMStrategy, "run", _run)
    ctx = ProposeContext(goal_id="g", task_id="t", task={"max_agent_loop_iterations": 3}, base_prompt="x")
    result = AgentLoopToolCallingStrategy().run(ctx)
    assert result.status == STATUS_EXECUTABLE
    assert result.metadata["agent_loop"]["iteration"] == 1
