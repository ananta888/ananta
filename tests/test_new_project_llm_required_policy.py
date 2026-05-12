"""Tests for LLM-required behavior of new_software_project — AFR-FINAL-T003.

Proves:
- new_software_project policy has llm_required=True
- orchestrator stops with needs_review when all LLM strategies are unavailable
- deterministic_handler is NOT reached when LLM is required but unavailable
- reachable mock LLM returns executable result
"""
from __future__ import annotations

import pytest
from unittest.mock import Mock

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
    LLM_STRATEGY_IDS,
    STRATEGY_TOOL_CALLING_LLM,
    STRATEGY_JSON_SCHEMA_LLM,
    STRATEGY_FLEXIBLE_LLM_NORMALIZATION,
    STRATEGY_DETERMINISTIC_HANDLER,
    LLM_MODE_PRIMARY_WITH_GUARDRAILS,
    get_task_kind_preset,
    build_policy_from_dict,
)


def _context() -> ProposeContext:
    return ProposeContext(
        goal_id="g-fib",
        task_id="t-fib",
        task={"task_kind": "new_software_project"},
        base_prompt="Create a Fibonacci REST API",
    )


def _llm_unavailable_strategy(sid: str) -> Mock:
    s = Mock(spec=ProposeStrategy)
    s.run.return_value = ProposeStrategyResult.declined(
        sid,
        reason=f"llm_required_but_unavailable: connection refused to {sid}",
        reason_codes=["llm_required", "llm_provider_unavailable"],
    )
    return s


def _llm_available_strategy(sid: str) -> Mock:
    proposal = ExecutableProposal.from_tool_calls(
        goal_id="g-fib", task_id="t-fib", strategy_id=sid,
        tool_calls=[{"name": "write_file", "args": {"path": "app.py", "content": "..."}}],
    )
    s = Mock(spec=ProposeStrategy)
    s.run.return_value = ProposeStrategyResult.executable(sid, proposal)
    return s


def _declining_non_llm_strategy(sid: str) -> Mock:
    s = Mock(spec=ProposeStrategy)
    s.run.return_value = ProposeStrategyResult.declined(sid, "not_applicable")
    return s


class TestNewProjectPolicyHasLlmRequired:
    """T003: new_software_project policy enforces LLM-required by default."""

    def test_preset_has_llm_mode_primary_with_guardrails(self):
        preset = get_task_kind_preset("new_software_project")
        assert preset.get("llm_mode") == LLM_MODE_PRIMARY_WITH_GUARDRAILS

    def test_policy_llm_required_property(self):
        preset = get_task_kind_preset("new_software_project")
        policy = build_policy_from_dict(preset)
        assert policy.llm_required is True

    def test_llm_strategies_are_first_in_order(self):
        preset = get_task_kind_preset("new_software_project")
        policy = build_policy_from_dict(preset)
        order = policy.effective_strategy_order()
        assert order[0] == STRATEGY_TOOL_CALLING_LLM
        assert order[1] == STRATEGY_JSON_SCHEMA_LLM

    def test_deterministic_handler_is_not_llm_strategy(self):
        assert STRATEGY_DETERMINISTIC_HANDLER not in LLM_STRATEGY_IDS


class TestLlmRequiredEnforcement:
    """T003: when all LLM strategies return unavailable, orchestrator returns needs_review."""

    def test_needs_review_when_all_llm_strategies_unavailable(self):
        order = [
            STRATEGY_TOOL_CALLING_LLM,
            STRATEGY_JSON_SCHEMA_LLM,
            STRATEGY_FLEXIBLE_LLM_NORMALIZATION,
            STRATEGY_DETERMINISTIC_HANDLER,
        ]
        policy = ProposePolicy(
            strategy_order=order,
            llm_mode=LLM_MODE_PRIMARY_WITH_GUARDRAILS,
            on_all_strategies_declined="needs_review",
        )
        strategies = {
            STRATEGY_TOOL_CALLING_LLM: _llm_unavailable_strategy(STRATEGY_TOOL_CALLING_LLM),
            STRATEGY_JSON_SCHEMA_LLM: _llm_unavailable_strategy(STRATEGY_JSON_SCHEMA_LLM),
            STRATEGY_FLEXIBLE_LLM_NORMALIZATION: _llm_unavailable_strategy(STRATEGY_FLEXIBLE_LLM_NORMALIZATION),
            STRATEGY_DETERMINISTIC_HANDLER: _declining_non_llm_strategy(STRATEGY_DETERMINISTIC_HANDLER),
        }
        orch = ProposeStrategyOrchestrator(policy, strategies)

        result = orch.run(_context())

        assert result.status == STATUS_NEEDS_REVIEW
        assert "llm_required_but_unavailable" in (result.reason or "")
        # Deterministic handler must NOT have been reached
        strategies[STRATEGY_DETERMINISTIC_HANDLER].run.assert_not_called()

    def test_llm_required_reason_codes_present(self):
        order = [STRATEGY_TOOL_CALLING_LLM, STRATEGY_JSON_SCHEMA_LLM, STRATEGY_FLEXIBLE_LLM_NORMALIZATION]
        policy = ProposePolicy(
            strategy_order=order,
            llm_mode=LLM_MODE_PRIMARY_WITH_GUARDRAILS,
            on_all_strategies_declined="needs_review",
        )
        strategies = {sid: _llm_unavailable_strategy(sid) for sid in order}
        orch = ProposeStrategyOrchestrator(policy, strategies)

        result = orch.run(_context())

        assert "llm_required" in result.reason_codes
        assert "llm_provider_unavailable" in result.reason_codes
        assert "no_llm_fallback_allowed" in result.reason_codes

    def test_llm_available_returns_executable_without_enforcement(self):
        order = [STRATEGY_TOOL_CALLING_LLM, STRATEGY_JSON_SCHEMA_LLM, STRATEGY_FLEXIBLE_LLM_NORMALIZATION]
        policy = ProposePolicy(
            strategy_order=order,
            llm_mode=LLM_MODE_PRIMARY_WITH_GUARDRAILS,
            on_all_strategies_declined="needs_review",
        )
        strategies = {
            STRATEGY_TOOL_CALLING_LLM: _llm_available_strategy(STRATEGY_TOOL_CALLING_LLM),
            STRATEGY_JSON_SCHEMA_LLM: _declining_non_llm_strategy(STRATEGY_JSON_SCHEMA_LLM),
            STRATEGY_FLEXIBLE_LLM_NORMALIZATION: _declining_non_llm_strategy(STRATEGY_FLEXIBLE_LLM_NORMALIZATION),
        }
        orch = ProposeStrategyOrchestrator(policy, strategies)

        result = orch.run(_context())

        assert result.status == STATUS_EXECUTABLE
        assert result.metadata.get("selected_strategy") == STRATEGY_TOOL_CALLING_LLM

    def test_partial_llm_unavailability_continues_chain(self):
        """Only ONE LLM strategy unavailable — chain continues to next LLM."""
        order = [STRATEGY_TOOL_CALLING_LLM, STRATEGY_JSON_SCHEMA_LLM]
        policy = ProposePolicy(
            strategy_order=order,
            llm_mode=LLM_MODE_PRIMARY_WITH_GUARDRAILS,
            on_all_strategies_declined="needs_review",
        )
        strategies = {
            STRATEGY_TOOL_CALLING_LLM: _llm_unavailable_strategy(STRATEGY_TOOL_CALLING_LLM),
            STRATEGY_JSON_SCHEMA_LLM: _llm_available_strategy(STRATEGY_JSON_SCHEMA_LLM),
        }
        orch = ProposeStrategyOrchestrator(policy, strategies)

        result = orch.run(_context())

        # json_schema_llm succeeded → executable
        assert result.status == STATUS_EXECUTABLE

    def test_non_llm_required_policy_falls_through_to_deterministic(self):
        """When llm_required=False, deterministic handler is reached after LLM fails."""
        from agent.services.propose_policy import LLM_MODE_FALLBACK
        order = [STRATEGY_TOOL_CALLING_LLM, STRATEGY_DETERMINISTIC_HANDLER]
        policy = ProposePolicy(
            strategy_order=order,
            llm_mode=LLM_MODE_FALLBACK,
            on_all_strategies_declined="needs_review",
        )
        proposal = ExecutableProposal.from_command(
            goal_id="g1", task_id="t1", strategy_id=STRATEGY_DETERMINISTIC_HANDLER,
            command="echo deterministic"
        )
        det_strategy = Mock(spec=ProposeStrategy)
        det_strategy.run.return_value = ProposeStrategyResult.executable(
            STRATEGY_DETERMINISTIC_HANDLER, proposal
        )
        strategies = {
            STRATEGY_TOOL_CALLING_LLM: _llm_unavailable_strategy(STRATEGY_TOOL_CALLING_LLM),
            STRATEGY_DETERMINISTIC_HANDLER: det_strategy,
        }
        orch = ProposeStrategyOrchestrator(policy, strategies)

        result = orch.run(_context())

        # Without llm_required, deterministic handler is reached
        assert result.status == STATUS_EXECUTABLE
        assert result.metadata.get("selected_strategy") == STRATEGY_DETERMINISTIC_HANDLER
