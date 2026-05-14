"""FA-T021: Tests for ProposeStrategyRegistry and real strategy wiring.

Verifies:
- No StubStrategy in the registry
- Each registered strategy is a real implementation
- Missing strategy_id handled by orchestrator (not silent stub)
- FlexibleLLMNormalizationStrategy integrates with LLMResponseNormalizer
- ToolCallingLLMStrategy / JsonSchemaLLMStrategy decline when LLM unavailable
- HumanReviewStrategy returns needs_review (terminal)
- AdvisoryProposalStrategy returns advisory with task prompt
"""
from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_context(task_id="t1", goal_id="g1", base_prompt="make a fibonacci API"):
    from worker.core.propose_orchestrator import ProposeContext
    return ProposeContext(
        goal_id=goal_id,
        task_id=task_id,
        task={"task_kind": "new_software_project"},
        base_prompt=base_prompt,
        research_context=None,
        cli_runner=None,
        tool_definitions_resolver=lambda: [
            {"name": "write_file", "description": "Write a file", "parameters": {"path": "str", "content": "str"}}
        ],
    )


# ── registry construction ─────────────────────────────────────────────────────

class TestProposeStrategyRegistry:
    def test_registry_contains_all_known_strategies(self):
        from agent.services.propose_strategy_registry import build_strategy_registry
        reg = build_strategy_registry()
        expected = {
            "agent_loop_tool_calling", "cli_agent_patch_strategy", "hermes_proposal_strategy",
            "deterministic_handler", "worker_strategy",
            "tool_calling_llm", "json_schema_llm",
            "flexible_llm_normalization", "repair_procedure_runner", "advisory_proposal", "human_review",
        }
        assert expected == set(reg.keys())

    def test_no_stub_strategy_in_registry(self):
        from agent.services.propose_strategy_registry import build_strategy_registry
        from worker.core.propose_orchestrator import StubStrategy
        reg = build_strategy_registry()
        for sid, strategy in reg.items():
            assert not isinstance(strategy, StubStrategy), (
                f"{sid!r} is still a StubStrategy — FA-T021 requires real implementation"
            )

    def test_all_strategies_are_propose_strategy_subclasses(self):
        from agent.services.propose_strategy_registry import build_strategy_registry
        from worker.core.propose_orchestrator import ProposeStrategy
        reg = build_strategy_registry()
        for sid, strategy in reg.items():
            assert isinstance(strategy, ProposeStrategy), f"{sid} is not a ProposeStrategy"

    def test_flexible_llm_is_real_class(self):
        from agent.services.propose_strategy_registry import build_strategy_registry
        from agent.services.propose_strategies.flexible_llm_normalization_strategy import FlexibleLLMNormalizationStrategy
        reg = build_strategy_registry()
        assert isinstance(reg["flexible_llm_normalization"], FlexibleLLMNormalizationStrategy)

    def test_human_review_is_real_class(self):
        from agent.services.propose_strategy_registry import build_strategy_registry
        from agent.services.propose_strategies.human_review_strategy import HumanReviewStrategy
        reg = build_strategy_registry()
        assert isinstance(reg["human_review"], HumanReviewStrategy)

    def test_advisory_proposal_is_real_class(self):
        from agent.services.propose_strategy_registry import build_strategy_registry
        from agent.services.propose_strategies.advisory_proposal_strategy import AdvisoryProposalStrategy
        reg = build_strategy_registry()
        assert isinstance(reg["advisory_proposal"], AdvisoryProposalStrategy)

    def test_missing_strategy_id_is_declined_with_diagnostics(self):
        """Orchestrator declines unregistered strategy_id — not silent stub success."""
        from agent.services.propose_strategy_registry import build_strategy_registry
        from worker.core.propose_orchestrator import ProposeStrategyOrchestrator, ProposeContext
        from agent.services.propose_policy import ProposePolicy

        reg = build_strategy_registry()
        policy = ProposePolicy(
            strategy_order=["nonexistent_strategy"],
            on_all_strategies_declined="needs_review",
            max_strategy_attempts=1,
        )
        orch = ProposeStrategyOrchestrator(policy, reg)
        result = orch.run(_make_context())
        assert result.status == "needs_review"
        attempted = result.metadata["attempted_strategies"]
        assert len(attempted) == 1
        assert attempted[0]["strategy_id"] == "nonexistent_strategy"
        assert attempted[0]["reason"] == "strategy_not_available"


# ── individual strategy tests ─────────────────────────────────────────────────

class TestHumanReviewStrategy:
    def test_returns_needs_review_terminal(self):
        from agent.services.propose_strategies.human_review_strategy import HumanReviewStrategy
        from worker.core.propose import STATUS_NEEDS_REVIEW
        result = HumanReviewStrategy().run(_make_context())
        assert result.status == STATUS_NEEDS_REVIEW
        assert result.is_terminal

    def test_metadata_contains_task_and_goal_ids(self):
        from agent.services.propose_strategies.human_review_strategy import HumanReviewStrategy
        result = HumanReviewStrategy().run(_make_context(task_id="t99", goal_id="g42"))
        assert result.metadata["task_id"] == "t99"
        assert result.metadata["goal_id"] == "g42"


class TestAdvisoryProposalStrategy:
    def test_returns_advisory_with_prompt(self):
        from agent.services.propose_strategies.advisory_proposal_strategy import AdvisoryProposalStrategy
        from worker.core.propose import STATUS_ADVISORY
        ctx = _make_context(task_id="t1", base_prompt="build fibonacci API")
        result = AdvisoryProposalStrategy().run(ctx)
        assert result.status == STATUS_ADVISORY
        assert "fibonacci" in result.advisory_text.lower()

    def test_not_executable(self):
        from agent.services.propose_strategies.advisory_proposal_strategy import AdvisoryProposalStrategy
        result = AdvisoryProposalStrategy().run(_make_context())
        assert not result.is_executable


class TestFlexibleLLMNormalizationStrategy:
    def test_declines_when_llm_unavailable(self):
        from agent.services.propose_strategies.flexible_llm_normalization_strategy import FlexibleLLMNormalizationStrategy
        from agent.services.model_invocation_service import LLMUnavailableError
        from worker.core.propose import STATUS_DECLINED

        with patch("agent.services.model_invocation_service.ModelInvocationService.invoke") as mock_invoke:
            mock_invoke.side_effect = LLMUnavailableError("llm_connection_failed")
            result = FlexibleLLMNormalizationStrategy().run(_make_context())

        assert result.status == STATUS_DECLINED
        assert "llm_required_but_unavailable" in result.reason

    def test_declines_on_empty_response(self):
        from agent.services.propose_strategies.flexible_llm_normalization_strategy import FlexibleLLMNormalizationStrategy
        from worker.core.propose import STATUS_DECLINED

        with patch("agent.services.model_invocation_service.ModelInvocationService.invoke", return_value="   "):
            result = FlexibleLLMNormalizationStrategy().run(_make_context())

        assert result.status == STATUS_DECLINED
        assert result.reason == "llm_returned_empty_response"

    def test_advisory_when_llm_returns_fenced_shell_without_shell_policy(self):
        """Shell blocks are advisory by default — policy.allow_shell_execution=False."""
        from agent.services.propose_strategies.flexible_llm_normalization_strategy import FlexibleLLMNormalizationStrategy
        from worker.core.propose import STATUS_ADVISORY

        raw = "Sure! Here is the command:\n```bash\nmkdir fibonacci-api && cd fibonacci-api\n```"
        with patch("agent.services.model_invocation_service.ModelInvocationService.invoke", return_value=raw):
            result = FlexibleLLMNormalizationStrategy().run(_make_context())

        assert result.status == STATUS_ADVISORY
        assert "shell_execution_not_allowed_by_policy" in result.reason

    def test_executable_when_llm_returns_fenced_shell_with_shell_policy(self):
        """Shell blocks become executable when policy.allow_shell_execution=True."""
        from agent.services.propose_strategies.flexible_llm_normalization_strategy import FlexibleLLMNormalizationStrategy
        from worker.core.propose_orchestrator import ProposeContext
        from agent.services.propose_policy import ProposePolicy
        from worker.core.propose import STATUS_EXECUTABLE

        policy = ProposePolicy(allow_shell_execution=True)
        ctx = ProposeContext(
            goal_id="g", task_id="t",
            task={"task_kind": "new_software_project"},
            base_prompt="make fibonacci",
            tool_definitions_resolver=lambda: [],
            policy=policy,
        )
        raw = "```bash\nmkdir fibonacci-api && cd fibonacci-api\n```"
        with patch("agent.services.model_invocation_service.ModelInvocationService.invoke", return_value=raw):
            result = FlexibleLLMNormalizationStrategy().run(ctx)

        assert result.status == STATUS_EXECUTABLE
        assert result.proposal.command is not None

    def test_advisory_when_llm_returns_prose(self):
        from agent.services.propose_strategies.flexible_llm_normalization_strategy import FlexibleLLMNormalizationStrategy
        from worker.core.propose import STATUS_ADVISORY

        raw = "To build a fibonacci API, you should start by creating a Flask app..."
        with patch("agent.services.model_invocation_service.ModelInvocationService.invoke", return_value=raw):
            result = FlexibleLLMNormalizationStrategy().run(_make_context())

        assert result.status == STATUS_ADVISORY

    def test_failed_on_unexpected_exception(self):
        from agent.services.propose_strategies.flexible_llm_normalization_strategy import FlexibleLLMNormalizationStrategy
        from worker.core.propose import STATUS_FAILED

        with patch("agent.services.model_invocation_service.ModelInvocationService.invoke") as mock_invoke:
            mock_invoke.side_effect = RuntimeError("unexpected error")
            result = FlexibleLLMNormalizationStrategy().run(_make_context())

        assert result.status == STATUS_FAILED


class TestToolCallingLLMStrategy:
    def test_declines_when_llm_unavailable(self):
        from worker.core.tool_calling_llm_strategy import ToolCallingLLMStrategy
        from agent.services.model_invocation_service import LLMUnavailableError
        from worker.core.propose import STATUS_DECLINED

        with patch("agent.services.model_invocation_service.ModelInvocationService.invoke_with_tools") as m:
            m.side_effect = LLMUnavailableError("connection_refused")
            with patch("agent.config.settings") as ms:
                ms.default_provider = "lmstudio"
                result = ToolCallingLLMStrategy().run(_make_context())

        assert result.status == STATUS_DECLINED
        assert "llm_required_but_unavailable" in result.reason

    def test_declines_when_no_tools_defined(self):
        from worker.core.tool_calling_llm_strategy import ToolCallingLLMStrategy
        from worker.core.propose_orchestrator import ProposeContext
        from worker.core.propose import STATUS_DECLINED

        ctx = ProposeContext(
            goal_id="g", task_id="t", task={}, base_prompt="x",
            tool_definitions_resolver=lambda: [],
        )
        with patch("agent.config.settings") as ms:
            ms.default_provider = "lmstudio"
            result = ToolCallingLLMStrategy().run(ctx)

        assert result.status == STATUS_DECLINED
        assert result.reason == "no_tools_defined"

    def test_declines_when_no_resolver(self):
        from worker.core.tool_calling_llm_strategy import ToolCallingLLMStrategy
        from worker.core.propose_orchestrator import ProposeContext
        from worker.core.propose import STATUS_DECLINED

        ctx = ProposeContext(goal_id="g", task_id="t", task={}, base_prompt="x")
        with patch("agent.config.settings") as ms:
            ms.default_provider = "lmstudio"
            result = ToolCallingLLMStrategy().run(ctx)

        assert result.status == STATUS_DECLINED
        assert result.reason == "no_tools_defined"

    def test_executable_when_llm_returns_tool_calls(self):
        from worker.core.tool_calling_llm_strategy import ToolCallingLLMStrategy
        from worker.core.propose import STATUS_EXECUTABLE

        mock_response = {
            "tool_calls": [{"name": "write_file", "args": {"path": "main.py", "content": "# hi"}}],
            "content": "",
        }
        with patch("agent.services.model_invocation_service.ModelInvocationService.invoke_with_tools", return_value=mock_response):
            with patch("agent.config.settings") as ms:
                ms.default_provider = "lmstudio"
                result = ToolCallingLLMStrategy().run(_make_context())

        assert result.status == STATUS_EXECUTABLE
        assert result.proposal.tool_calls[0]["name"] == "write_file"

    def test_declines_for_mock_provider(self):
        from worker.core.tool_calling_llm_strategy import ToolCallingLLMStrategy
        from worker.core.propose import STATUS_DECLINED

        with patch("agent.config.settings") as ms:
            ms.default_provider = "mock"
            result = ToolCallingLLMStrategy().run(_make_context())

        assert result.status == STATUS_DECLINED
        assert "mock" in result.reason


class TestJsonSchemaLLMStrategy:
    def test_declines_when_llm_unavailable(self):
        from worker.core.json_schema_llm_strategy import JsonSchemaLLMStrategy
        from agent.services.model_invocation_service import LLMUnavailableError
        from worker.core.propose import STATUS_DECLINED

        with patch("agent.services.model_invocation_service.ModelInvocationService.invoke_with_json_schema") as m:
            m.side_effect = LLMUnavailableError("timeout")
            with patch("agent.config.settings") as ms:
                ms.default_provider = "lmstudio"
                result = JsonSchemaLLMStrategy().run(_make_context())

        assert result.status == STATUS_DECLINED
        assert "llm_required_but_unavailable" in result.reason

    def test_executable_when_llm_returns_command(self):
        import json as _json
        from worker.core.json_schema_llm_strategy import JsonSchemaLLMStrategy
        from worker.core.propose import STATUS_EXECUTABLE

        payload = _json.dumps({"command": "echo hello world", "tool_calls": []})
        with patch("agent.services.model_invocation_service.ModelInvocationService.invoke_with_json_schema", return_value=payload):
            with patch("agent.config.settings") as ms:
                ms.default_provider = "lmstudio"
                result = JsonSchemaLLMStrategy().run(_make_context())

        assert result.status == STATUS_EXECUTABLE
        assert result.proposal.command == "echo hello world"


# ── new_software_project policy: no stub in default chain ─────────────────────

class TestNewSoftwareProjectNoStub:
    """Prove no StubStrategy is used in the default new_software_project policy."""

    def test_no_stub_in_new_software_project_strategy_chain(self):
        from agent.services.propose_strategy_registry import build_strategy_registry
        from worker.core.propose_orchestrator import StubStrategy
        from agent.services.propose_policy_service import get_propose_policy_service

        policy = get_propose_policy_service().get_effective_policy(
            task_kind="new_software_project"
        )
        reg = build_strategy_registry()

        for strategy_id in policy.effective_strategy_order():
            if strategy_id in reg:
                assert not isinstance(reg[strategy_id], StubStrategy), (
                    f"Strategy {strategy_id!r} in new_software_project chain is a StubStrategy"
                )

    def test_new_software_project_policy_has_llm_strategies(self):
        from agent.services.propose_policy_service import get_propose_policy_service
        policy = get_propose_policy_service().get_effective_policy(
            task_kind="new_software_project"
        )
        order = policy.effective_strategy_order()
        assert "tool_calling_llm" in order or "json_schema_llm" in order or "flexible_llm_normalization" in order

    def test_orchestrator_with_real_registry_declines_gracefully_when_llm_down(self):
        """With LLM unavailable, orchestrator reaches human_review — not error."""
        from agent.services.propose_strategy_registry import build_strategy_registry
        from worker.core.propose_orchestrator import ProposeStrategyOrchestrator, ProposeContext
        from agent.services.propose_policy import ProposePolicy
        from agent.services.model_invocation_service import LLMUnavailableError
        from worker.core.propose import STATUS_NEEDS_REVIEW

        # All LLM calls fail; deterministic_handler also has no handler for this kind
        reg = build_strategy_registry()
        policy = ProposePolicy(
            strategy_order=["tool_calling_llm", "json_schema_llm", "flexible_llm_normalization", "human_review"],
            on_all_strategies_declined="needs_review",
            max_strategy_attempts=4,
        )
        orch = ProposeStrategyOrchestrator(policy, reg)

        ctx = ProposeContext(
            goal_id="g", task_id="t", task={}, base_prompt="make fibonacci API",
            tool_definitions_resolver=lambda: [{"name": "write_file", "description": "d", "parameters": {}}],
        )

        with patch("agent.services.model_invocation_service.ModelInvocationService._make_chat_call") as mock_call:
            mock_call.side_effect = LLMUnavailableError("connection refused")
            with patch("agent.config.settings") as ms:
                ms.default_provider = "lmstudio"
                result = orch.run(ctx)

        # human_review is terminal — must reach it
        assert result.status == STATUS_NEEDS_REVIEW
        assert result.metadata["selected_strategy"] == "human_review"
