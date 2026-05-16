"""Regression: raw prose from LLM must never become executable — AFR-FINAL-T009.

Proves that plain natural language, markdown, and partially-structured text cannot
bypass the validation layer and become executable command/tool_calls.
"""
from __future__ import annotations

import pytest
from unittest.mock import Mock

from worker.core.propose_orchestrator import ProposeContext
from worker.core.propose import (
    ExecutableProposal,
    ProposeStrategyResult,
    validate_executable_proposal,
    STATUS_ADVISORY,
    STATUS_DECLINED,
    STATUS_EXECUTABLE,
)
from agent.services.llm_response_normalizer import LLMResponseNormalizer


def _context() -> ProposeContext:
    return ProposeContext(goal_id="g1", task_id="t1", task={}, base_prompt="test")


PROSE_SAMPLES = [
    "Great idea! You should create a FastAPI application with a /fib endpoint.",
    "To implement the Fibonacci API, follow these steps:\n1. Install FastAPI\n2. Create main.py",
    "I recommend using Flask for this project. The API should have GET /fibonacci/<n>.",
    "Sure, I can help you create a Fibonacci API. Let me outline the approach...",
    "The best approach would be to use Python with FastAPI. Here's what you need to do.",
    "Certainly! Creating a REST API for Fibonacci numbers is straightforward.",
    "",
    "   \n\n   ",
]


class TestNormalizerProseFallback:
    """LLMResponseNormalizer must classify all prose samples as advisory."""

    @pytest.fixture
    def normalizer(self):
        return LLMResponseNormalizer()

    @pytest.mark.parametrize("prose", [s for s in PROSE_SAMPLES if s.strip()])
    def test_prose_becomes_advisory_not_executable(self, normalizer, prose):
        result = normalizer.normalize(prose, _context())
        assert result.status != STATUS_EXECUTABLE, (
            f"Prose incorrectly became executable: {prose[:80]!r}"
        )
        assert result.status == STATUS_ADVISORY

    def test_prose_advisory_has_no_executable_proposal(self, normalizer):
        result = normalizer.normalize("Please create a Fibonacci API.", _context())
        assert result.status == STATUS_ADVISORY
        assert result.proposal is None

    def test_malformed_json_in_fenced_block_is_advisory(self, normalizer):
        raw = "```json\n{ command: not-valid-json\n```"
        result = normalizer.normalize(raw, _context())
        assert result.status == STATUS_ADVISORY
        assert result.proposal is None

    def test_json_without_command_or_tool_calls_is_declined(self):
        from worker.core.json_schema_llm_strategy import JsonSchemaLLMStrategy
        import json
        ctx = Mock(spec=ProposeContext)
        ctx.goal_id = "g1"
        ctx.task_id = "t1"
        ctx.task = {}
        ctx.base_prompt = "test"

        strategy = JsonSchemaLLMStrategy()
        with pytest.MonkeyPatch().context() as m:
            m.setattr("agent.config.settings.default_provider", "lmstudio")
            m.setattr(
                "agent.services.model_invocation_service.ModelInvocationService.invoke_with_json_schema_result",
                Mock(return_value=json.dumps({"message": "Here is your API"})),
            )
            result = strategy.run(ctx)

        # Non-executable JSON (no command, no tool_calls) → declined
        assert result.status == STATUS_DECLINED

    def test_shell_block_without_allow_is_advisory(self, normalizer):
        raw = "```bash\nrm -rf /tmp/old-data\n```"
        result = normalizer.normalize(raw, _context(), allow_shell_execution=False)
        assert result.status == STATUS_ADVISORY
        assert result.proposal is None

    def test_shell_block_with_allow_is_executable(self, normalizer):
        raw = "```bash\nmkdir project\n```"
        result = normalizer.normalize(raw, _context(), allow_shell_execution=True)
        assert result.status == STATUS_EXECUTABLE
        assert result.proposal is not None
        assert isinstance(result.proposal, ExecutableProposal)


class TestValidateRejectsProse:
    """validate_executable_proposal must reject dicts without command/tool_calls."""

    def test_prose_only_dict_rejected(self):
        raw = {"message": "Create a Fibonacci API with FastAPI"}
        with pytest.raises(ValueError, match="executable_proposal_requires_command_or_tool_calls"):
            validate_executable_proposal(raw)

    def test_description_key_not_command(self):
        raw = {"description": "echo hello", "reason": "some_reason"}
        with pytest.raises(ValueError):
            validate_executable_proposal(raw)

    def test_string_value_not_accepted(self):
        with pytest.raises(ValueError, match="invalid_proposal_type"):
            validate_executable_proposal("echo hello")

    def test_integer_value_not_accepted(self):
        with pytest.raises(ValueError, match="invalid_proposal_type"):
            validate_executable_proposal(42)

    def test_empty_dict_rejected(self):
        with pytest.raises(ValueError):
            validate_executable_proposal({})


class TestToolCallingRejectsProse:
    """ToolCallingLLMStrategy must not accept prose as executable output."""

    def test_tool_calls_missing_name_rejected(self, monkeypatch):
        from worker.core.tool_calling_llm_strategy import ToolCallingLLMStrategy
        ctx = Mock(spec=ProposeContext)
        ctx.tool_definitions_resolver.return_value = [{"name": "write_file"}]
        ctx.goal_id = "g1"
        ctx.task_id = "t1"
        ctx.task = {}
        ctx.base_prompt = "test"

        monkeypatch.setattr("agent.config.settings.default_provider", "lmstudio")
        monkeypatch.setattr(
            "agent.services.model_invocation_service.ModelInvocationService.invoke_with_tools",
            Mock(return_value={"tool_calls": [{"args": {}}], "finish_reason": "tool_calls"}),
        )
        strategy = ToolCallingLLMStrategy()
        result = strategy.run(ctx)
        # tool_calls without 'name' must be rejected
        assert result.status == STATUS_DECLINED
        assert "tool_calls_missing_names" in result.reason
