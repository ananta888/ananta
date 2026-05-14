"""Tests for JsonSchemaLLMStrategy — FA-T009/AFR-T004."""

import json
import pytest
from unittest.mock import Mock

from worker.core.propose_orchestrator import ProposeContext
from worker.core.propose import (
    STATUS_DECLINED,
    STATUS_ADVISORY,
    STATUS_EXECUTABLE,
    STATUS_FAILED,
)
from worker.core.json_schema_llm_strategy import JsonSchemaLLMStrategy
from agent.services.model_invocation_service import LLMUnavailableError


class TestJsonSchemaLLMStrategy:
    @pytest.fixture
    def context(self):
        ctx = Mock(spec=ProposeContext)
        ctx.goal_id = "g1"
        ctx.task_id = "t1"
        ctx.task = {}
        ctx.base_prompt = "Create a Fibonacci API"
        return ctx

    def test_declined_mock_provider(self, context, monkeypatch):
        monkeypatch.setattr("agent.config.settings.default_provider", "mock")
        strategy = JsonSchemaLLMStrategy()
        result = strategy.run(context)
        assert result.status == STATUS_DECLINED
        assert "provider_json_schema_not_supported_mock" in result.reason

    def test_declined_llm_unavailable(self, context, monkeypatch):
        monkeypatch.setattr("agent.config.settings.default_provider", "lmstudio")
        mock_svc = Mock(side_effect=LLMUnavailableError("timeout"))
        monkeypatch.setattr(
            "agent.services.model_invocation_service.ModelInvocationService.invoke_with_json_schema",
            mock_svc,
        )
        strategy = JsonSchemaLLMStrategy()
        result = strategy.run(context)
        assert result.status == STATUS_DECLINED
        assert "llm_required_but_unavailable" in result.reason
        assert "llm_provider_unavailable" in result.reason_codes

    def test_executable_tool_calls(self, context, monkeypatch):
        monkeypatch.setattr("agent.config.settings.default_provider", "lmstudio")
        mock_svc = Mock(return_value=json.dumps({
            "tool_calls": [{"name": "write_file", "args": {"path": "schema.py"}}],
        }))
        monkeypatch.setattr(
            "agent.services.model_invocation_service.ModelInvocationService.invoke_with_json_schema",
            mock_svc,
        )
        strategy = JsonSchemaLLMStrategy()
        result = strategy.run(context)
        assert result.status == STATUS_EXECUTABLE
        assert result.proposal.tool_calls[0]["name"] == "write_file"
        call_kwargs = mock_svc.call_args.kwargs
        assert "Prompt context bundle (JSON):" in call_kwargs["prompt"]
        assert result.proposal.metadata["prompt_context_bundle"]["schema"] == "prompt_context_bundle.v1"

    def test_executable_command(self, context, monkeypatch):
        monkeypatch.setattr("agent.config.settings.default_provider", "lmstudio")
        mock_svc = Mock(return_value=json.dumps({"command": "pip install fastapi"}))
        monkeypatch.setattr(
            "agent.services.model_invocation_service.ModelInvocationService.invoke_with_json_schema",
            mock_svc,
        )
        strategy = JsonSchemaLLMStrategy()
        result = strategy.run(context)
        assert result.status == STATUS_EXECUTABLE
        assert result.proposal.command == "pip install fastapi"

    def test_declined_empty_json_no_output(self, context, monkeypatch):
        monkeypatch.setattr("agent.config.settings.default_provider", "lmstudio")
        mock_svc = Mock(return_value=json.dumps({}))
        monkeypatch.setattr(
            "agent.services.model_invocation_service.ModelInvocationService.invoke_with_json_schema",
            mock_svc,
        )
        strategy = JsonSchemaLLMStrategy()
        result = strategy.run(context)
        # empty JSON has no command or tool_calls → declined (not advisory)
        assert result.status == STATUS_DECLINED
        assert "llm_returned_no_executable_output" in result.reason

    def test_advisory_invalid_json_parse_failure(self, context, monkeypatch):
        monkeypatch.setattr("agent.config.settings.default_provider", "lmstudio")
        mock_svc = Mock(return_value="invalid { json malformed")
        monkeypatch.setattr(
            "agent.services.model_invocation_service.ModelInvocationService.invoke_with_json_schema",
            mock_svc,
        )
        strategy = JsonSchemaLLMStrategy()
        result = strategy.run(context)
        assert result.status == STATUS_ADVISORY
        assert "json_parse_failed" in result.reason

    def test_failed_unexpected_exception(self, context, monkeypatch):
        monkeypatch.setattr("agent.config.settings.default_provider", "lmstudio")
        mock_svc = Mock(side_effect=Exception("service down"))
        monkeypatch.setattr(
            "agent.services.model_invocation_service.ModelInvocationService.invoke_with_json_schema",
            mock_svc,
        )
        strategy = JsonSchemaLLMStrategy()
        result = strategy.run(context)
        assert result.status == STATUS_FAILED
        assert "llm_call_failed" in result.reason
