"""AFR-FINAL-T008: E2E test — new-project Fibonacci uses LLM-first path, no sgpt.

Proves the full chain with mocked OpenAI-compatible provider:
  ProposeStrategyOrchestrator
    → ToolCallingLLMStrategy (mocked provider → tool_calls)
    → ExecutableProposal with tool_calls
    → selected_strategy == "tool_calling_llm"

sgpt is patched to raise in the entire module — if called, the test fails.
The mocked provider simulates a local Ollama/LMStudio endpoint returning tool_calls.
"""
from __future__ import annotations

import pytest
from unittest.mock import Mock, patch

from worker.core.propose_orchestrator import ProposeContext, ProposeStrategyOrchestrator
from worker.core.propose import ExecutableProposal, STATUS_EXECUTABLE
from agent.services.propose_policy import (
    get_task_kind_preset,
    build_policy_from_dict,
    STRATEGY_TOOL_CALLING_LLM,
    STRATEGY_JSON_SCHEMA_LLM,
    STRATEGY_FLEXIBLE_LLM_NORMALIZATION,
)
from agent.services.propose_strategy_registry import build_strategy_registry
from agent.services.model_invocation_service import LLMUnavailableError

from tests.fixtures.mock_openai_compatible_provider import (
    make_mock_invoke_with_tools,
    make_mock_invoke_with_json_schema,
    make_mock_invoke,
)


FIBONACCI_TOOL_CALLS = [
    {"name": "write_file", "args": {"path": "app.py", "content": "from flask import Flask\napp=Flask(__name__)\n"}},
    {"name": "write_file", "args": {"path": "requirements.txt", "content": "flask>=2.0\n"}},
    {"name": "write_file", "args": {"path": "README.md", "content": "# Fibonacci API\n"}},
]


@pytest.fixture(autouse=True)
def _block_sgpt():
    """sgpt must never be called in this test module."""
    with patch("agent.common.sgpt.run_sgpt_command",
               side_effect=RuntimeError("sgpt_blocked_in_AFR-T008"), create=True):
        yield


@pytest.fixture
def fibonacci_context():
    return ProposeContext(
        goal_id="goal-fib-llm-001",
        task_id="task-fib-llm-001",
        task={"task_kind": "new_software_project"},
        base_prompt="Create a Fibonacci REST API",
        tool_definitions_resolver=lambda: [
            {"name": "write_file", "description": "Write content to a file"},
        ],
        policy=None,
    )


class TestNewProjectLLMFirstWithMockedProvider:
    """Proves the LLM-first path through ToolCallingLLMStrategy."""

    def test_tool_calling_llm_selected_for_new_project(self, fibonacci_context, monkeypatch):
        """selected_strategy is tool_calling_llm when provider returns valid tool_calls."""
        monkeypatch.setattr("agent.config.settings.default_provider", "lmstudio")
        monkeypatch.setattr(
            "agent.services.model_invocation_service.ModelInvocationService.invoke_with_tools",
            make_mock_invoke_with_tools(FIBONACCI_TOOL_CALLS),
        )

        preset = get_task_kind_preset("new_software_project")
        policy = build_policy_from_dict(preset)
        fibonacci_context.policy = policy

        registry = build_strategy_registry()
        orch = ProposeStrategyOrchestrator(policy, registry)

        result = orch.run(fibonacci_context)

        assert result.status == STATUS_EXECUTABLE, (
            f"Expected executable, got {result.status}: {result.reason}"
        )
        assert isinstance(result.proposal, ExecutableProposal)
        assert result.metadata.get("selected_strategy") == STRATEGY_TOOL_CALLING_LLM

    def test_tool_calls_contain_fibonacci_files(self, fibonacci_context, monkeypatch):
        monkeypatch.setattr("agent.config.settings.default_provider", "lmstudio")
        monkeypatch.setattr(
            "agent.services.model_invocation_service.ModelInvocationService.invoke_with_tools",
            make_mock_invoke_with_tools(FIBONACCI_TOOL_CALLS),
        )

        preset = get_task_kind_preset("new_software_project")
        policy = build_policy_from_dict(preset)
        fibonacci_context.policy = policy

        registry = build_strategy_registry()
        orch = ProposeStrategyOrchestrator(policy, registry)
        result = orch.run(fibonacci_context)

        assert result.is_executable
        tc_paths = [tc["args"]["path"] for tc in result.proposal.tool_calls if tc.get("args")]
        assert "app.py" in tc_paths
        assert "requirements.txt" in tc_paths

    def test_no_sgpt_in_tool_calling_path(self, fibonacci_context, monkeypatch):
        """sgpt must not be invoked — _block_sgpt fixture ensures this."""
        monkeypatch.setattr("agent.config.settings.default_provider", "lmstudio")
        monkeypatch.setattr(
            "agent.services.model_invocation_service.ModelInvocationService.invoke_with_tools",
            make_mock_invoke_with_tools(FIBONACCI_TOOL_CALLS),
        )

        preset = get_task_kind_preset("new_software_project")
        policy = build_policy_from_dict(preset)
        fibonacci_context.policy = policy

        registry = build_strategy_registry()
        orch = ProposeStrategyOrchestrator(policy, registry)
        result = orch.run(fibonacci_context)

        # If sgpt was called, _block_sgpt would have raised RuntimeError and the test fails
        assert result.is_executable

    def test_deterministic_handler_not_selected_before_llm(self, fibonacci_context, monkeypatch):
        """For new_software_project, LLM runs first — deterministic only after LLM strategies."""
        monkeypatch.setattr("agent.config.settings.default_provider", "lmstudio")
        monkeypatch.setattr(
            "agent.services.model_invocation_service.ModelInvocationService.invoke_with_tools",
            make_mock_invoke_with_tools(FIBONACCI_TOOL_CALLS),
        )

        preset = get_task_kind_preset("new_software_project")
        policy = build_policy_from_dict(preset)
        fibonacci_context.policy = policy

        registry = build_strategy_registry()
        orch = ProposeStrategyOrchestrator(policy, registry)
        result = orch.run(fibonacci_context)

        assert result.metadata.get("selected_strategy") != "deterministic_handler", (
            "deterministic_handler was selected before LLM strategies — LLM-first policy violated"
        )

    def test_proposal_schema_matches_execute_contract(self, fibonacci_context, monkeypatch):
        """ExecutableProposal returned by tool_calling_llm can be validated by execute path."""
        from worker.core.propose import validate_executable_proposal
        monkeypatch.setattr("agent.config.settings.default_provider", "lmstudio")
        monkeypatch.setattr(
            "agent.services.model_invocation_service.ModelInvocationService.invoke_with_tools",
            make_mock_invoke_with_tools(FIBONACCI_TOOL_CALLS),
        )

        preset = get_task_kind_preset("new_software_project")
        policy = build_policy_from_dict(preset)
        fibonacci_context.policy = policy

        registry = build_strategy_registry()
        orch = ProposeStrategyOrchestrator(policy, registry)
        result = orch.run(fibonacci_context)

        assert result.is_executable
        proposal_dict = result.proposal.to_dict()
        cmd, tcs, _ = validate_executable_proposal(proposal_dict)
        assert tcs, "Persisted proposal dict must have tool_calls reloadable by execute"


class TestNewProjectFallsBackToJsonSchemaLLM:
    """If tool_calling_llm declines (no tool support), json_schema_llm is tried."""

    def test_json_schema_llm_selected_when_tool_calling_declines(self, fibonacci_context, monkeypatch):
        monkeypatch.setattr("agent.config.settings.default_provider", "lmstudio")
        # tool_calling_llm: no tool_calls returned → declined
        monkeypatch.setattr(
            "agent.services.model_invocation_service.ModelInvocationService.invoke_with_tools",
            Mock(return_value={"tool_calls": [], "finish_reason": "stop"}),
        )
        # json_schema_llm: returns a command
        monkeypatch.setattr(
            "agent.services.model_invocation_service.ModelInvocationService.invoke_with_json_schema_result",
            make_mock_invoke_with_json_schema("mkdir fibonacci-api"),
        )

        preset = get_task_kind_preset("new_software_project")
        policy = build_policy_from_dict(preset)
        fibonacci_context.policy = policy

        registry = build_strategy_registry()
        orch = ProposeStrategyOrchestrator(policy, registry)
        result = orch.run(fibonacci_context)

        assert result.status == STATUS_EXECUTABLE
        assert result.metadata.get("selected_strategy") == STRATEGY_JSON_SCHEMA_LLM


class TestNewProjectLLMRequired:
    """When all LLM strategies are unavailable, result is needs_review, not fake success."""

    def test_needs_review_when_all_llm_unavailable(self, fibonacci_context, monkeypatch):
        monkeypatch.setattr("agent.config.settings.default_provider", "lmstudio")
        monkeypatch.setattr(
            "agent.services.model_invocation_service.ModelInvocationService.invoke_with_tools",
            Mock(side_effect=LLMUnavailableError("connection refused")),
        )
        monkeypatch.setattr(
            "agent.services.model_invocation_service.ModelInvocationService.invoke_with_json_schema_result",
            Mock(side_effect=LLMUnavailableError("connection refused")),
        )
        monkeypatch.setattr(
            "agent.services.model_invocation_service.ModelInvocationService.invoke",
            Mock(side_effect=LLMUnavailableError("connection refused")),
        )

        preset = get_task_kind_preset("new_software_project")
        policy = build_policy_from_dict(preset)
        fibonacci_context.policy = policy

        registry = build_strategy_registry()
        orch = ProposeStrategyOrchestrator(policy, registry)
        result = orch.run(fibonacci_context)

        assert result.status in ("needs_review", "failed"), (
            f"Expected needs_review or failed when LLM unavailable, got: {result.status}"
        )
        assert not result.is_executable
        reason_text = (result.reason or "") + " ".join(result.reason_codes or [])
        assert "llm" in reason_text.lower(), (
            f"Result reason must mention LLM unavailability, got: {result.reason}"
        )
