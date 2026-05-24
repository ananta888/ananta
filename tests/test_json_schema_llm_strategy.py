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
        ctx.effective_config = None
        ctx.rendered_system_prompt = "STACK POLICY\nDo not bypass governance."
        ctx.instruction_stack = {"checksum": "json-stack-1"}
        ctx.instruction_diagnostics = {"applied_layers": [{"layer": "governance"}]}
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
            "agent.services.model_invocation_service.ModelInvocationService.invoke_with_json_schema_result",
            mock_svc,
        )
        strategy = JsonSchemaLLMStrategy()
        result = strategy.run(context)
        assert result.status == STATUS_DECLINED
        assert "llm_required_but_unavailable" in result.reason
        assert "llm_provider_unavailable" in result.reason_codes

    def test_executable_tool_calls(self, context, monkeypatch):
        monkeypatch.setattr("agent.config.settings.default_provider", "lmstudio")
        mock_svc = Mock(return_value={
            "content": json.dumps({
                "tool_calls": [{"name": "write_file", "args": {"path": "schema.py"}}],
            }),
            "provider": "ollama",
            "model": "qwen2.5",
            "metadata": {"llm_call_profile": [{"source": "model_invocation_service", "estimated": False}]},
        })
        monkeypatch.setattr(
            "agent.services.model_invocation_service.ModelInvocationService.invoke_with_json_schema_result",
            mock_svc,
        )
        strategy = JsonSchemaLLMStrategy()
        result = strategy.run(context)
        assert result.status == STATUS_EXECUTABLE
        assert result.proposal.tool_calls[0]["name"] == "write_file"
        call_kwargs = mock_svc.call_args.kwargs
        assert "Respond with valid JSON:" in call_kwargs["prompt"]
        assert call_kwargs["system_prompt"].count("STACK POLICY") == 1
        assert result.proposal.metadata["prompt_context_bundle"]["schema"] == "prompt_context_bundle.v1"
        assert result.proposal.metadata["prompt_context_bundle"]["instruction_stack_checksum"] == "json-stack-1"
        assert result.proposal.metadata["provider"] == "ollama"
        assert result.proposal.metadata["model"] == "qwen2.5"
        assert result.proposal.metadata["llm_call_profile"][0]["estimated"] is False

    def test_executable_command(self, context, monkeypatch):
        monkeypatch.setattr("agent.config.settings.default_provider", "lmstudio")
        mock_svc = Mock(return_value={"content": json.dumps({"command": "pip install fastapi"})})
        monkeypatch.setattr(
            "agent.services.model_invocation_service.ModelInvocationService.invoke_with_json_schema_result",
            mock_svc,
        )
        strategy = JsonSchemaLLMStrategy()
        result = strategy.run(context)
        assert result.status == STATUS_EXECUTABLE
        assert result.proposal.command == "pip install fastapi"

    def test_declined_empty_json_no_output(self, context, monkeypatch):
        monkeypatch.setattr("agent.config.settings.default_provider", "lmstudio")
        mock_svc = Mock(
            return_value={
                "content": json.dumps({}),
                "metadata": {"llm_call_profile": [{"source": "model_invocation_service", "estimated": False}]},
            }
        )
        monkeypatch.setattr(
            "agent.services.model_invocation_service.ModelInvocationService.invoke_with_json_schema_result",
            mock_svc,
        )
        strategy = JsonSchemaLLMStrategy()
        result = strategy.run(context)
        # empty JSON has no command or tool_calls → declined (not advisory)
        assert result.status == STATUS_DECLINED
        assert "llm_returned_no_executable_output" in result.reason
        assert result.metadata["llm_call_profile"][0]["estimated"] is False

    def test_advisory_invalid_json_parse_failure(self, context, monkeypatch):
        monkeypatch.setattr("agent.config.settings.default_provider", "lmstudio")
        mock_svc = Mock(
            return_value={
                "content": "invalid { json malformed",
                "metadata": {"llm_call_profile": [{"source": "model_invocation_service", "estimated": False}]},
            }
        )
        monkeypatch.setattr(
            "agent.services.model_invocation_service.ModelInvocationService.invoke_with_json_schema_result",
            mock_svc,
        )
        strategy = JsonSchemaLLMStrategy()
        result = strategy.run(context)
        assert result.status == STATUS_ADVISORY
        assert "json_parse_failed" in result.reason
        assert result.metadata["llm_call_profile"][0]["estimated"] is False

    def test_failed_unexpected_exception(self, context, monkeypatch):
        monkeypatch.setattr("agent.config.settings.default_provider", "lmstudio")
        mock_svc = Mock(side_effect=Exception("service down"))
        monkeypatch.setattr(
            "agent.services.model_invocation_service.ModelInvocationService.invoke_with_json_schema_result",
            mock_svc,
        )
        strategy = JsonSchemaLLMStrategy()
        result = strategy.run(context)
        assert result.status == STATUS_FAILED
        assert "llm_call_failed" in result.reason
        assert result.metadata["llm_call_profile"][0]["success"] is False


# TRM-003: effective_config propagation in JsonSchemaLLMStrategy
class TestJsonSchemaEffectiveConfigPropagation:
    @pytest.fixture
    def ctx(self):
        ctx = Mock(spec=ProposeContext)
        ctx.goal_id = "g-eff"
        ctx.task_id = "t-eff"
        ctx.task = {}
        ctx.base_prompt = "Do something"
        ctx.effective_config = {"default_provider": "ollama", "default_model": "eff-model"}
        return ctx

    def test_effective_config_mock_provider_declines(self, ctx, monkeypatch):
        ctx.effective_config = {"default_provider": "mock"}
        monkeypatch.setattr("agent.config.settings.default_provider", "ollama")
        strategy = JsonSchemaLLMStrategy()
        result = strategy.run(ctx)
        assert result.status == STATUS_DECLINED
        assert "not_supported_mock" in result.reason

    def test_effective_config_real_provider_invokes_llm(self, ctx, monkeypatch):
        monkeypatch.setattr("agent.config.settings.default_provider", "mock")
        mock_invoke = Mock(return_value={
            "content": json.dumps({"tool_calls": [{"name": "write_file", "args": {"path": "f.py", "content": ""}}]}),
            "provider": "ollama",
            "model": "eff-model",
            "metadata": {"llm_call_profile": [{"source": "model_invocation_service", "estimated": False, "success": True}]},
        })
        monkeypatch.setattr(
            "agent.services.model_invocation_service.ModelInvocationService.invoke_with_json_schema_result",
            mock_invoke,
        )
        strategy = JsonSchemaLLMStrategy()
        result = strategy.run(ctx)
        # effective_config overrides settings' "mock" to "ollama" — LLM should be invoked
        assert mock_invoke.called, "invoke must be called when effective_config provider is real"
        assert result.status in (STATUS_EXECUTABLE, STATUS_DECLINED)  # declined only if tool-name filtered
