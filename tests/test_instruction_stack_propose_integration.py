from __future__ import annotations

from unittest.mock import Mock

from worker.core.propose_orchestrator import ProposeContext
from worker.core.tool_calling_llm_strategy import ToolCallingLLMStrategy


def test_tool_calling_strategy_uses_instruction_stack_prompt_and_checksum(monkeypatch):
    context = Mock(spec=ProposeContext)
    context.goal_id = "g-int"
    context.task_id = "t-int"
    context.task = {"task_kind": "coding", "description": "Implement feature"}
    context.base_prompt = "Implement feature X"
    context.policy = Mock(allow_shell_execution=False)
    context.effective_config = {"default_provider": "lmstudio"}
    context.tool_definitions_resolver.return_value = [{"name": "write_file"}]
    context.rendered_system_prompt = "INSTRUCTION STACK\nFollow governance."
    context.instruction_stack = {"checksum": "stack-int-1"}
    context.instruction_diagnostics = {"applied_layers": [{"layer": "governance"}]}

    mock_llm = Mock(
        return_value={
            "tool_calls": [{"name": "write_file", "args": {"path": "a.py", "content": "print(1)"}}],
            "finish_reason": "tool_calls",
            "provider": "lmstudio",
            "model": "test-model",
            "metadata": {"llm_call_profile": [{"source": "model_invocation_service", "estimated": False}]},
        }
    )
    monkeypatch.setattr(
        "agent.services.model_invocation_service.ModelInvocationService.invoke_with_tools",
        mock_llm,
    )

    result = ToolCallingLLMStrategy().run(context)
    assert result.status == "executable"
    assert result.proposal.metadata["prompt_context_bundle"]["instruction_stack_checksum"] == "stack-int-1"
    assert "INSTRUCTION STACK" in mock_llm.call_args.kwargs["system_prompt"]

