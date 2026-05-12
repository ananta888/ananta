"""Tests for JsonSchemaLLMStrategy — FA-T009."""

import pytest
from unittest.mock import Mock

from worker.core.propose_orchestrator import ProposeContext
from worker.core.propose import (
    ProposeStrategyResult,
    STATUS_DECLINED,
    STATUS_ADVISORY,
    STATUS_EXECUTABLE,
    STATUS_FAILED,
)
from worker.core.json_schema_llm_strategy import JsonSchemaLLMStrategy


class TestJsonSchemaLLMStrategy:
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

    def test_declined_no_tools(self, context_no_tools):
        strategy = JsonSchemaLLMStrategy()
        result = strategy.run(context_no_tools)
        assert result.status == STATUS_DECLINED
        assert "no_tools_defined" in result.reason

    def test_declined_provider_not_supported(self, context_with_tools, monkeypatch):
        monkeypatch.setattr(
            "worker.core.json_schema_llm_strategy.JsonSchemaLLMStrategy.SUPPORTED_PROVIDERS",
            set(),
        )
        strategy = JsonSchemaLLMStrategy()
        result = strategy.run(context_with_tools)
        assert result.status == STATUS_DECLINED
        assert "provider_json_schema_not_supported" in result.reason

    def test_executable_tool_calls(self, context_with_tools, monkeypatch):
        import json
        mock_response = json.dumps({
            "tool_calls": [{"name": "write_file", "args": {"path": "schema.py"}}]
        })
        mock_service = Mock(return_value=mock_response)
        monkeypatch.setattr(
            "agent.services.model_invocation_service.ModelInvocationService.invoke_with_json_schema",
            mock_service,
        )

        strategy = JsonSchemaLLMStrategy()
        result = strategy.run(context_with_tools)

        assert result.status == STATUS_EXECUTABLE
        proposal = result.proposal
        assert proposal.tool_calls[0]["name"] == "write_file"

    def test_executable_command(self, context_with_tools, monkeypatch):
        import json
        mock_response = json.dumps({"command": "pip install fastapi"})
        mock_service = Mock(return_value=mock_response)
        monkeypatch.setattr(
            "agent.services.model_invocation_service.ModelInvocationService.invoke_with_json_schema",
            mock_service,
        )

        strategy = JsonSchemaLLMStrategy()
        result = strategy.run(context_with_tools)

        assert result.status == STATUS_EXECUTABLE
        proposal = result.proposal
        assert proposal.command == "pip install fastapi"

    def test_advisory_empty_parsed(self, context_with_tools, monkeypatch):
        import json
        mock_response = json.dumps({})
        mock_service = Mock(return_value=mock_response)
        monkeypatch.setattr(
            "agent.services.model_invocation_service.ModelInvocationService.invoke_with_json_schema",
            mock_service,
        )

        strategy = JsonSchemaLLMStrategy()
        result = strategy.run(context_with_tools)

        assert result.status == STATUS_ADVISORY
        assert "{}" in result.advisory_text

    def test_advisory_invalid_json(self, context_with_tools, monkeypatch):
        mock_response = "invalid { json"
        mock_service = Mock(return_value=mock_response)
        monkeypatch.setattr(
            "agent.services.model_invocation_service.ModelInvocationService.invoke_with_json_schema",
            mock_service,
        )

        strategy = JsonSchemaLLMStrategy()
        result = strategy.run(context_with_tools)

        assert result.status == STATUS_ADVISORY
        assert "invalid" in result.advisory_text

    def test_failed_exception(self, context_with_tools, monkeypatch):
        mock_service = Mock(side_effect=Exception("service down"))
        monkeypatch.setattr(
            "agent.services.model_invocation_service.ModelInvocationService.invoke_with_json_schema",
            mock_service,
        )

        strategy = JsonSchemaLLMStrategy()
        result = strategy.run(context_with_tools)

        assert result.status == STATUS_FAILED
        assert "llm_call_failed" in result.reason
