"""Tests for ToolCallingLLMStrategy — FA-T009/AFR-T004."""

from unittest.mock import Mock

import pytest

from agent.services.model_invocation_service import LLMUnavailableError
from worker.core.propose import (
    STATUS_ADVISORY,
    STATUS_DECLINED,
    STATUS_EXECUTABLE,
    STATUS_FAILED,
)
from worker.core.propose_orchestrator import ProposeContext
from worker.core.tool_calling_llm_strategy import ToolCallingLLMStrategy


class TestToolCallingLLMStrategy:
    @pytest.fixture
    def context_no_tools(self):
        ctx = Mock(spec=ProposeContext)
        ctx.tool_definitions_resolver.return_value = []
        ctx.goal_id = "g1"
        ctx.task_id = "t1"
        ctx.task = {}
        ctx.base_prompt = "test"
        ctx.effective_config = None
        return ctx

    @pytest.fixture
    def context_with_tools(self):
        ctx = Mock(spec=ProposeContext)
        ctx.tool_definitions_resolver.return_value = [{"name": "write_file"}]
        ctx.goal_id = "g1"
        ctx.task_id = "t1"
        ctx.task = {}
        ctx.base_prompt = "test"
        ctx.policy = Mock(allow_shell_execution=False)
        ctx.effective_config = None
        ctx.rendered_system_prompt = "POLICY BLOCK\nUse German."
        ctx.instruction_stack = {"checksum": "stack-xyz"}
        ctx.instruction_diagnostics = {"applied_layers": [{"layer": "governance"}]}
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
        mock_llm = Mock(
            side_effect=LLMUnavailableError(
                "connection refused",
                llm_call_profile=[{"profile_id": "gemma", "error_type": "connection_error"}],
                fallback_decisions=[
                    {
                        "reason": "candidate_chain_exhausted",
                        "previous_profile_id": "gemma",
                        "trigger": "provider_unavailable",
                        "terminal": True,
                    }
                ],
                terminal_reason="provider_unavailable",
            )
        )
        monkeypatch.setattr(
            "agent.services.model_invocation_service.ModelInvocationService.invoke_with_tools",
            mock_llm,
        )
        strategy = ToolCallingLLMStrategy()
        result = strategy.run(context_with_tools)
        assert result.status == STATUS_DECLINED
        assert "llm_required_but_unavailable" in result.reason
        assert "llm_provider_unavailable" in result.reason_codes
        assert result.metadata["model_recovery_signal"]["state"] == "exhausted"
        assert result.metadata["fallback_decisions"][0]["reason"] == "candidate_chain_exhausted"

    def test_invalid_explicit_routing_is_a_terminal_policy_decision(self, context_with_tools, monkeypatch):
        monkeypatch.setattr("agent.config.settings.default_provider", "ollama")
        context_with_tools.task = {"model_routing": "invalid"}
        mock_llm = Mock()
        monkeypatch.setattr(
            "agent.services.model_invocation_service.ModelInvocationService.invoke_with_tools",
            mock_llm,
        )

        result = ToolCallingLLMStrategy().run(context_with_tools)

        assert result.status == STATUS_DECLINED
        assert result.reason == "model_routing_policy_blocked"
        assert result.metadata["fallback_decisions"][0]["trigger"] == "policy_blocked"
        assert result.metadata["fallback_decisions"][0]["terminal"] is True
        mock_llm.assert_not_called()

    def test_declined_empty_tool_calls(self, context_with_tools, monkeypatch):
        monkeypatch.setattr("agent.config.settings.default_provider", "lmstudio")
        mock_llm = Mock(
            return_value={
                "tool_calls": [],
                "finish_reason": "stop",
                "metadata": {"llm_call_profile": [{"source": "model_invocation_service", "estimated": False}]},
            }
        )
        monkeypatch.setattr(
            "agent.services.model_invocation_service.ModelInvocationService.invoke_with_tools",
            mock_llm,
        )
        strategy = ToolCallingLLMStrategy()
        result = strategy.run(context_with_tools)
        assert result.status == STATUS_DECLINED
        assert "tools_not_supported" in result.reason_codes
        assert result.metadata["llm_call_profile"][0]["estimated"] is False

    def test_declined_no_tool_calls_returned(self, context_with_tools, monkeypatch):
        monkeypatch.setattr("agent.config.settings.default_provider", "lmstudio")
        mock_llm = Mock(
            return_value={
                "tool_calls": [],
                "finish_reason": "unknown",
                "metadata": {"llm_call_profile": [{"source": "model_invocation_service", "estimated": False}]},
            }
        )
        monkeypatch.setattr(
            "agent.services.model_invocation_service.ModelInvocationService.invoke_with_tools",
            mock_llm,
        )
        strategy = ToolCallingLLMStrategy()
        result = strategy.run(context_with_tools)
        assert result.status == STATUS_DECLINED
        assert "llm_returned_no_tool_calls" in result.reason
        assert result.metadata["llm_call_profile"][0]["estimated"] is False

    def test_executable_valid_tool_calls(self, context_with_tools, monkeypatch):
        monkeypatch.setattr("agent.config.settings.default_provider", "lmstudio")
        mock_llm = Mock(
            return_value={
                "tool_calls": [{"name": "write_file", "args": {"path": "main.py", "content": "def fib(): pass"}}],
                "finish_reason": "tool_calls",
                "provider": "ollama",
                "model": "qwen2.5",
                "metadata": {"llm_call_profile": [{"source": "model_invocation_service", "estimated": False}]},
            }
        )
        monkeypatch.setattr(
            "agent.services.model_invocation_service.ModelInvocationService.invoke_with_tools",
            mock_llm,
        )
        strategy = ToolCallingLLMStrategy()
        result = strategy.run(context_with_tools)
        assert result.status == STATUS_EXECUTABLE
        assert result.proposal.tool_calls[0]["name"] == "write_file"
        assert result.proposal.metadata["provider"] == "ollama"
        assert result.proposal.metadata["model"] == "qwen2.5"
        assert result.proposal.metadata["llm_call_profile"][0]["estimated"] is False
        call_kwargs = mock_llm.call_args.kwargs
        assert "Prompt context bundle:" in call_kwargs["system_prompt"]
        assert call_kwargs["system_prompt"].count("POLICY BLOCK") == 1
        assert result.proposal.metadata["prompt_context_bundle"]["instruction_stack_checksum"] == "stack-xyz"

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
        assert result.metadata["llm_call_profile"][0]["success"] is False

    def test_declined_invalid_tool_call_name(self, context_with_tools, monkeypatch):
        monkeypatch.setattr("agent.config.settings.default_provider", "lmstudio")
        mock_llm = Mock(
            return_value={
                "tool_calls": [{"name": "some_placeholder_tool", "args": {"argument": None}}],
                "finish_reason": "tool_calls",
                "provider": "ollama",
                "model": "qwen2.5",
            }
        )
        monkeypatch.setattr(
            "agent.services.model_invocation_service.ModelInvocationService.invoke_with_tools",
            mock_llm,
        )
        strategy = ToolCallingLLMStrategy()
        result = strategy.run(context_with_tools)
        assert result.status == STATUS_DECLINED
        assert result.reason == "tool_calls_invalid_or_missing_names"

    def test_executable_without_instruction_stack_still_builds_prompt(self, context_with_tools, monkeypatch):
        monkeypatch.setattr("agent.config.settings.default_provider", "lmstudio")
        context_with_tools.rendered_system_prompt = None
        context_with_tools.instruction_stack = None
        context_with_tools.instruction_diagnostics = None
        mock_llm = Mock(
            return_value={
                "tool_calls": [{"name": "write_file", "args": {"path": "main.py", "content": "print(1)"}}],
                "finish_reason": "tool_calls",
                "provider": "ollama",
                "model": "qwen2.5",
                "metadata": {"llm_call_profile": [{"source": "model_invocation_service", "estimated": False}]},
            }
        )
        monkeypatch.setattr(
            "agent.services.model_invocation_service.ModelInvocationService.invoke_with_tools",
            mock_llm,
        )
        result = ToolCallingLLMStrategy().run(context_with_tools)
        assert result.status == STATUS_EXECUTABLE
        call_kwargs = mock_llm.call_args.kwargs
        assert "Prompt context bundle:" in call_kwargs["system_prompt"]

    def test_content_fallback_inline_shell_denied_by_policy(self, context_with_tools, monkeypatch):
        monkeypatch.setattr("agent.config.settings.default_provider", "lmstudio")
        context_with_tools.policy.allow_shell_execution = False
        mock_llm = Mock(return_value={"tool_calls": [], "content": "`echo ok`", "finish_reason": "stop"})
        monkeypatch.setattr(
            "agent.services.model_invocation_service.ModelInvocationService.invoke_with_tools",
            mock_llm,
        )
        strategy = ToolCallingLLMStrategy()
        result = strategy.run(context_with_tools)
        assert result.status == STATUS_ADVISORY
        assert result.proposal is None
        assert result.reason == "shell_execution_not_allowed_by_policy"
        assert result.metadata["source"] == "tool_calling_llm_content_fallback"
        assert result.metadata["allow_shell_execution"] is False

    def test_content_fallback_inline_shell_allowed_by_policy(self, context_with_tools, monkeypatch):
        monkeypatch.setattr("agent.config.settings.default_provider", "lmstudio")
        context_with_tools.policy.allow_shell_execution = True
        mock_llm = Mock(return_value={"tool_calls": [], "content": "`echo ok`", "finish_reason": "stop"})
        monkeypatch.setattr(
            "agent.services.model_invocation_service.ModelInvocationService.invoke_with_tools",
            mock_llm,
        )
        strategy = ToolCallingLLMStrategy()
        result = strategy.run(context_with_tools)
        assert result.status == STATUS_EXECUTABLE
        assert result.proposal.command == "echo ok"
        assert result.metadata["source"] == "tool_calling_llm_content_fallback"
        assert result.metadata["allow_shell_execution"] is True

    def test_content_fallback_fenced_shell_denied_by_policy(self, context_with_tools, monkeypatch):
        monkeypatch.setattr("agent.config.settings.default_provider", "lmstudio")
        context_with_tools.policy.allow_shell_execution = False
        mock_llm = Mock(return_value={"tool_calls": [], "content": "```bash\necho ok\n```", "finish_reason": "stop"})
        monkeypatch.setattr(
            "agent.services.model_invocation_service.ModelInvocationService.invoke_with_tools",
            mock_llm,
        )
        strategy = ToolCallingLLMStrategy()
        result = strategy.run(context_with_tools)
        assert result.status == STATUS_ADVISORY
        assert result.reason == "shell_execution_not_allowed_by_policy"

    def test_content_fallback_inline_json_tool_calls_without_shell_permission(self, context_with_tools, monkeypatch):
        monkeypatch.setattr("agent.config.settings.default_provider", "lmstudio")
        context_with_tools.policy.allow_shell_execution = False
        mock_llm = Mock(
            return_value={
                "tool_calls": [],
                "content": '{"tool_calls":[{"name":"write_file","args":{"path":"main.py","content":"print(1)"}}]}',
                "finish_reason": "stop",
            }
        )
        monkeypatch.setattr(
            "agent.services.model_invocation_service.ModelInvocationService.invoke_with_tools",
            mock_llm,
        )
        strategy = ToolCallingLLMStrategy()
        result = strategy.run(context_with_tools)
        assert result.status == STATUS_EXECUTABLE
        assert result.proposal.tool_calls[0]["name"] == "write_file"
        assert result.metadata["source"] == "tool_calling_llm_content_fallback"
        assert result.metadata["allow_shell_execution"] is False


# TRM-003: effective_config.default_provider is preferred over settings
class TestToolCallingEffectiveConfigPropagation:
    @pytest.fixture
    def ctx_with_effective_config(self):
        ctx = Mock(spec=ProposeContext)
        ctx.tool_definitions_resolver.return_value = [{"name": "write_file"}]
        ctx.goal_id = "g-eff"
        ctx.task_id = "t-eff"
        ctx.task = {}
        ctx.base_prompt = "test"
        ctx.policy = Mock(allow_shell_execution=False)
        ctx.effective_config = {"default_provider": "ollama", "default_model": "custom-model"}
        return ctx

    def test_effective_config_mock_provider_is_declined(self, ctx_with_effective_config, monkeypatch):
        """When effective_config sets provider to a mock-only provider, strategy must decline."""
        ctx_with_effective_config.effective_config = {"default_provider": "mock"}
        monkeypatch.setattr("agent.config.settings.default_provider", "ollama")
        strategy = ToolCallingLLMStrategy()
        result = strategy.run(ctx_with_effective_config)
        assert result.status == STATUS_DECLINED
        assert "provider_tools_not_supported_mock" in result.reason

    def test_effective_config_real_provider_proceeds_to_invoke(self, ctx_with_effective_config, monkeypatch):
        """When effective_config sets a real provider, the strategy invokes the LLM."""
        monkeypatch.setattr("agent.config.settings.default_provider", "mockllm")  # settings says mock
        attempt_plan = [
            {
                "profile_id": "phi",
                "binding_id": "provider-binding:" + "a" * 64,
                "provider_id": "ollama",
                "model_id": "phi4-mini",
                "maximum_attempts": 3,
            }
        ]
        ctx_with_effective_config.effective_config.update(
            {
                "provider_context": {"provider_profile_id": "phi"},
                "provider_contexts_by_profile_id": {"phi": {"provider_profile_id": "phi"}},
                "provider_attempt_plan": attempt_plan,
            }
        )
        mock_invoke = Mock(
            return_value={
                "tool_calls": [{"name": "write_file", "args": {"path": "x.py", "content": ""}}],
                "finish_reason": "tool_calls",
                "provider": "ollama",
                "model": "test-model",
                "metadata": {"llm_call_profile": [{"source": "model_invocation_service", "estimated": False}]},
            }
        )
        monkeypatch.setattr(
            "agent.services.model_invocation_service.ModelInvocationService.invoke_with_tools",
            mock_invoke,
        )
        strategy = ToolCallingLLMStrategy()
        result = strategy.run(ctx_with_effective_config)
        # effective_config says "ollama" (real), so the strategy should proceed despite settings saying mock
        assert result.status == STATUS_EXECUTABLE
        assert mock_invoke.called, "invoke_with_tools must be called when effective_config provider is real"
        call_kwargs = mock_invoke.call_args.kwargs
        assert call_kwargs["provider_context"] == {"provider_profile_id": "phi"}
        assert call_kwargs["provider_contexts_by_profile_id"] == {"phi": {"provider_profile_id": "phi"}}
        assert call_kwargs["provider_attempt_plan"] == attempt_plan

    def test_llm_call_profile_present_in_executable_result(self, ctx_with_effective_config, monkeypatch):
        monkeypatch.setattr("agent.config.settings.default_provider", "lmstudio")
        monkeypatch.setattr(
            "agent.services.model_invocation_service.ModelInvocationService.invoke_with_tools",
            Mock(
                return_value={
                    "tool_calls": [{"name": "write_file", "args": {"path": "f.py", "content": ""}}],
                    "finish_reason": "tool_calls",
                    "provider": "ollama",
                    "model": "test-model",
                    "metadata": {
                        "llm_call_profile": [
                            {
                                "source": "model_invocation_service",
                                "estimated": False,
                                "success": True,
                                "latency_ms": 500,
                            }
                        ]
                    },
                }
            ),
        )
        strategy = ToolCallingLLMStrategy()
        result = strategy.run(ctx_with_effective_config)
        profile = result.proposal.metadata.get("llm_call_profile") or result.metadata.get("llm_call_profile")
        assert profile is not None, "llm_call_profile must be forwarded to result"
        assert profile[0]["source"] == "model_invocation_service"
