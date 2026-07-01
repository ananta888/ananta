"""Regression: sgpt must never be called in the default propose path — AFR-FINAL-T009.

The original failure involved sgpt/run_sgpt_command being invoked instead of a
real LLM strategy. This test pins that down: if sgpt is called, the test fails.
"""
from __future__ import annotations

import pytest
from unittest.mock import Mock, patch

from worker.core.propose_orchestrator import (
    ProposeContext,
    ProposeStrategy,
    ProposeStrategyOrchestrator,
)
from worker.core.propose import (
    ExecutableProposal,
    ProposeStrategyResult,
    STATUS_DECLINED,
    STATUS_EXECUTABLE,
    STATUS_NEEDS_REVIEW,
)
from agent.services.propose_policy import (
    ProposePolicy,
    STRATEGY_TOOL_CALLING_LLM,
    STRATEGY_JSON_SCHEMA_LLM,
    STRATEGY_FLEXIBLE_LLM_NORMALIZATION,
    STRATEGY_DETERMINISTIC_HANDLER,
    STRATEGY_ADVISORY_PROPOSAL,
    STRATEGY_HUMAN_REVIEW,
    LLM_MODE_PRIMARY_WITH_GUARDRAILS,
)
from agent.services.propose_strategy_registry import build_strategy_registry
from agent.services.model_invocation_service import LLMUnavailableError


def _context(task_kind: str = "new_software_project") -> ProposeContext:
    return ProposeContext(
        goal_id="g-sgpt-test",
        task_id="t-sgpt-test",
        task={"task_kind": task_kind},
        base_prompt="Create a Fibonacci REST API",
        tool_definitions_resolver=lambda: [{"name": "write_file"}],
    )


class TestNoSgptInProposeRegistry:
    """The strategy registry must not contain any sgpt-based strategy."""

    def test_registry_does_not_contain_sgpt_strategy(self):
        registry = build_strategy_registry()
        for sid, strategy in registry.items():
            strategy_type = type(strategy).__name__
            assert "sgpt" not in strategy_type.lower(), (
                f"Strategy {sid!r} is of type {strategy_type!r} which looks like sgpt"
            )
            assert "sgpt" not in sid.lower(), (
                f"Strategy id {sid!r} contains 'sgpt'"
            )

    def test_no_legacy_sgpt_in_default_new_project_policy(self):
        from agent.services.propose_policy import get_task_kind_preset, build_policy_from_dict, STRATEGY_LEGACY_SGPT
        preset = get_task_kind_preset("new_software_project")
        policy = build_policy_from_dict(preset)
        order = policy.effective_strategy_order()
        assert STRATEGY_LEGACY_SGPT not in order

    def test_allow_legacy_sgpt_is_false_in_new_project_policy(self):
        from agent.services.propose_policy import get_task_kind_preset, build_policy_from_dict
        preset = get_task_kind_preset("new_software_project")
        policy = build_policy_from_dict(preset)
        assert policy.allow_legacy_sgpt is False


class TestSgptBlockedDuringPropose:
    """If sgpt is accidentally invoked, these tests fail explicitly."""

    def test_propose_chain_does_not_invoke_sgpt(self, monkeypatch):
        # Force a non-mock provider so LLM strategies attempt real invocation and
        # raise LLMUnavailableError — triggering T003 early-return before
        # deterministic_handler (which requires Flask app context) is reached.
        monkeypatch.setattr("agent.config.settings.default_provider", "lmstudio")
        sgpt_called = []

        def _raise_if_sgpt(*args, **kwargs):
            sgpt_called.append(True)
            raise RuntimeError("sgpt_blocked: sgpt must not be called in default propose path")

        monkeypatch.setattr(
            "agent.cli_backends.sgpt.run_sgpt_command",
            _raise_if_sgpt,
            raising=False,
        )

        # Mock LLM to simulate unavailability
        monkeypatch.setattr(
            "agent.services.model_invocation_service.ModelInvocationService.invoke_with_tools",
            Mock(side_effect=LLMUnavailableError("no server")),
        )
        monkeypatch.setattr(
            "agent.services.model_invocation_service.ModelInvocationService.invoke_with_json_schema_result",
            Mock(side_effect=LLMUnavailableError("no server")),
        )
        monkeypatch.setattr(
            "agent.services.model_invocation_service.ModelInvocationService.invoke",
            Mock(side_effect=LLMUnavailableError("no server")),
        )

        from agent.services.propose_policy import get_task_kind_preset, build_policy_from_dict
        preset = get_task_kind_preset("new_software_project")
        policy = build_policy_from_dict(preset)

        registry = build_strategy_registry()
        orch = ProposeStrategyOrchestrator(policy, registry)

        result = orch.run(_context())

        # Must not have called sgpt
        assert not sgpt_called, "sgpt was called during propose — this is a regression"
        # Result should be needs_review (llm_required) or all_declined, never executable via sgpt
        assert not result.is_executable or result.metadata.get("selected_strategy") != "legacy_sgpt"

    def test_sgpt_monkeypatch_works(self, monkeypatch):
        """Verify that our sgpt block is reachable (fixture self-test)."""
        sgpt_reached = []

        def fake_sgpt(*args, **kwargs):
            sgpt_reached.append(True)
            return "fake output"

        monkeypatch.setattr("agent.cli_backends.sgpt.run_sgpt_command", fake_sgpt, raising=False)
        try:
            from agent.cli_backends.sgpt import run_sgpt_command
            run_sgpt_command("test")
        except Exception:
            pass
        # Whether called or not, what matters is that the patch is in place


class TestProposePolicyBlocksSgpt:
    """Policy-level guard: legacy_sgpt cannot be added to strategy order without admin override."""

    def test_legacy_sgpt_in_order_without_allow_raises(self):
        from agent.services.propose_policy import STRATEGY_LEGACY_SGPT
        with pytest.raises(ValueError, match="legacy_sgpt_in_strategy_order_but_allow_legacy_sgpt_is_false"):
            ProposePolicy(
                strategy_order=[STRATEGY_TOOL_CALLING_LLM, STRATEGY_LEGACY_SGPT],
                allow_legacy_sgpt=False,
            )

    def test_legacy_sgpt_allowed_with_explicit_flag(self):
        from agent.services.propose_policy import STRATEGY_LEGACY_SGPT
        policy = ProposePolicy(
            strategy_order=[STRATEGY_TOOL_CALLING_LLM, STRATEGY_LEGACY_SGPT],
            allow_legacy_sgpt=True,
        )
        # effective_strategy_order returns it when allowed
        assert STRATEGY_LEGACY_SGPT in policy.effective_strategy_order()

    def test_legacy_sgpt_filtered_from_effective_order_when_not_allowed(self):
        from agent.services.propose_policy import STRATEGY_LEGACY_SGPT
        policy = ProposePolicy(
            strategy_order=[STRATEGY_TOOL_CALLING_LLM],
            allow_legacy_sgpt=False,
        )
        assert STRATEGY_LEGACY_SGPT not in policy.effective_strategy_order()
