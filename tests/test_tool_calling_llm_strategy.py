"""Tests for ToolCallingLLMStrategy — FA-T009."""

import pytest
from unittest.mock import Mock, MagicMock

from worker.core.propose_orchestrator import ProposeContext
from worker.core.propose import (
    ProposeStrategyResult,
    STATUS_DECLINED,
    STATUS_ADVISORY,
    STATUS_EXECUTABLE,
    STATUS_FAILED,
)
from worker.core.tool_calling_llm_strategy import ToolCallingLLMStrategy


class TestToolCallingLLMStrategy:
    @pytest.fixture
    def context_no_tools(self):
        ctx = Mock(ProposeContext)
        ctx.tool_definitions_resolver.return_value = []
        ctx.goal_id = "g1"
        ctx.task_id = "t1"
        ctx.task = {}
        ctx.base_prompt = "test"
        return ctx

    @pytest.fixture
    def context_with_tools(self):
        ctx = Mock(ProposeContext)
        ctx.tool_definitions_resolver.return_value = [{"name": "write_file"}]
        ctx.goal_id = "g1"
        ctx.task_id = "t1"
        ctx.task = {}
        ctx.base_prompt = "test"
        return ctx

    def test_declined_no_tools(self, context_no_tools):
        strategy = ToolCallingLLMStrategy()
        result = strategy.run(context_no_tools)
        assert result.status == STATUS_DECLINED
        assert "no_tools_defined" in result.reason

    def test_declined_provider_not_supported(self, context_with_tools, monkeypatch):
        monkeypatch.setattr("worker.core.tool_calling_llm_strategy.ToolCallingLLMStrategy.SUPPORTED_PROVIDERS", set())
        strategy = ToolCallingLLMStrategy()
        result = strategy.run(context_with_tools)
        assert result.status == STATUS_DECLINED
        assert "provider_tools_not_supported" in result.reason

    def test_advisory_text_only(self, context_with_tools, monkeypatch):
        mock_llm = Mock(return_value={"content": "Propose a Fibonacci API with FastAPI.", "tool_calls": []})
        monkeypatch.setattr(
            "agent.services.model_invocation_service.ModelInvocationService.invoke_with_tools",
            mock_llm,
        )

        strategy = ToolCallingLLMStrategy()
        result = strategy.run(context_with_tools)

        assert result.status == STATUS_ADVISORY
        assert "Propose a Fibonacci API" in result.advisory_text

    def test_executable_valid_tool_calls(self, context_with_tools):
        strategy = ToolCallingLLMStrategy()
        result = strategy.run(context_with_tools)

        assert result.status == STATUS_EXECUTABLE
        proposal = result.proposal
        assert proposal.tool_calls
        assert proposal.tool_calls[0]["name"] == "write_file"
        assert "main.py" in proposal.tool_calls[0]["args"]["path"]

    def test_executable_invalid_tool(self, context_with_tools, monkeypatch):
        mock_llm = Mock(return_value={"tool_calls": [{"name": "invalid_tool"}]})
        monkeypatch.setattr(
            "agent.services.model_invocation_service.ModelInvocationService.invoke_with_tools",
            mock_llm,
        )

        strategy = ToolCallingLLMStrategy()
        result = strategy.run(context_with_tools)

        assert result.status == STATUS_EXECUTABLE  # Currently accepts invalid, TODO filter
        proposal = result.proposal
        assert proposal.tool_calls[0]["name"] == "invalid_tool"

    def test_failed_llm_call_exception(self, context_with_tools, monkeypatch):
        mock_llm = Mock(side_effect=Exception("llm error"))
        monkeypatch.setattr(
            "agent.services.model_invocation_service.ModelInvocationService.invoke_with_tools",
            mock_llm,
        )

        strategy = ToolCallingLLMStrategy()
        result = strategy.run(context_with_tools)

        assert result.status == STATUS_FAILED
        assert "llm_call_failed" in result.reason
