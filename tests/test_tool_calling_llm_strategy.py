"""Tests for ToolCallingLLMStrategy — FA-T009/AFR-T004."""

import pytest
from unittest.mock import Mock, patch

from worker.core.propose_orchestrator import ProposeContext
from worker.core.propose import (
    ProposeStrategyResult,
    STATUS_DECLINED,
    STATUS_EXECUTABLE,
    STATUS_FAILED,
)
from worker.core.tool_calling_llm_strategy import ToolCallingLLMStrategy
from agent.services.model_invocation_service import LLMUnavailableError


class TestToolCallingLLMStrategy:
    @pytest.fixture
    def context_no_tools(self):
        ctx = Mock(spec=ProposeContext)
        ctx.tool_definitions_resolver.return_value = []
        ctx.goal_id = "g1"
        ctx.task_id = "t1"
        ctx.task = {}
        ctx.base_prompt = "test"
        return ctx

    @pytest.fixture
    def context_with_tools(self):
        ctx = Mock(spec=ProposeContext)
        ctx.tool_definitions_resolver.return_value = [{"name": "write_file"}]
        ctx.goal_id = "g1"
        ctx.task_id = "t1"
        ctx.task = {}
        ctx.base_prompt = "test"
        return ctx

    def test_declined_no_tools(self, context_no_tools, monkeypatch):
        monkeypatch.setattr("agent.config.settings.default_provider", "lmstudio")
        strategy = ToolCallingLLMStrategy()
        result = strategy.run(context_no_tools)
        assert result.status == STATUS_DECLINED
        assert "no_tools_defined" in result.reason

    def test_declined_mock_provider(self, context_with_tools, monkeypatch):
        monkeypatch.setattr("agent.config.settings.default_provider", "mock")
        strategy = ToolCallingLLMStrategy()
        result = strategy.run(context_with_tools)
        assert result.status == STATUS_DECLINED
        assert "provider_tools_not_supported_mock" in result.reason

    def test_declined_llm_unavailable(self, context_with_tools, monkeypatch):
        monkeypatch.setattr("agent.config.settings.default_provider", "lmstudio")
        mock_llm = Mock(side_effect=LLMUnavailableError("connection refused"))
        monkeypatch.setattr(
            "agent.services.model_invocation_service.ModelInvocationService.invoke_with_tools",
            mock_llm,
        )
        strategy = ToolCallingLLMStrategy()
        result = strategy.run(context_with_tools)
        assert result.status == STATUS_DECLINED
        assert "llm_required_but_unavailable" in result.reason
        assert "llm_provider_unavailable" in result.reason_codes

    def test_declined_empty_tool_calls(self, context_with_tools, monkeypatch):
        monkeypatch.setattr("agent.config.settings.default_provider", "lmstudio")
        mock_llm = Mock(return_value={"tool_calls": [], "finish_reason": "stop"})
        monkeypatch.setattr(
            "agent.services.model_invocation_service.ModelInvocationService.invoke_with_tools",
            mock_llm,
        )
        strategy = ToolCallingLLMStrategy()
        result = strategy.run(context_with_tools)
        assert result.status == STATUS_DECLINED
        assert "tools_not_supported" in result.reason_codes

    def test_declined_no_tool_calls_returned(self, context_with_tools, monkeypatch):
        monkeypatch.setattr("agent.config.settings.default_provider", "lmstudio")
        mock_llm = Mock(return_value={"tool_calls": [], "finish_reason": "unknown"})
        monkeypatch.setattr(
            "agent.services.model_invocation_service.ModelInvocationService.invoke_with_tools",
            mock_llm,
        )
        strategy = ToolCallingLLMStrategy()
        result = strategy.run(context_with_tools)
        assert result.status == STATUS_DECLINED
        assert "llm_returned_no_tool_calls" in result.reason

    def test_executable_valid_tool_calls(self, context_with_tools, monkeypatch):
        monkeypatch.setattr("agent.config.settings.default_provider", "lmstudio")
        mock_llm = Mock(return_value={
            "tool_calls": [{"name": "write_file", "args": {"path": "main.py", "content": "def fib(): pass"}}],
            "finish_reason": "tool_calls",
        })
        monkeypatch.setattr(
            "agent.services.model_invocation_service.ModelInvocationService.invoke_with_tools",
            mock_llm,
        )
        strategy = ToolCallingLLMStrategy()
        result = strategy.run(context_with_tools)
        assert result.status == STATUS_EXECUTABLE
        assert result.proposal.tool_calls[0]["name"] == "write_file"

    def test_failed_llm_call_exception(self, context_with_tools, monkeypatch):
        monkeypatch.setattr("agent.config.settings.default_provider", "lmstudio")
        mock_llm = Mock(side_effect=Exception("unexpected error"))
        monkeypatch.setattr(
            "agent.services.model_invocation_service.ModelInvocationService.invoke_with_tools",
            mock_llm,
        )
        strategy = ToolCallingLLMStrategy()
        result = strategy.run(context_with_tools)
        assert result.status == STATUS_FAILED
        assert "llm_call_failed" in result.reason
