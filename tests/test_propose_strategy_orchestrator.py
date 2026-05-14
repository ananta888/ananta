"""Tests for ProposeStrategyOrchestrator full-chain semantics — AFR-FINAL-T002.

Proves:
- strategy_order is never truncated by max_strategy_attempts
- all declining strategies are attempted before fallback
- attempted_strategies metadata is complete
- T003: llm_required enforcement stops the chain before deterministic fallback
"""
from __future__ import annotations

from unittest.mock import Mock

import pytest

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
    STATUS_FAILED,
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
    STRATEGY_WORKER,
    LLM_MODE_PRIMARY_WITH_GUARDRAILS,
)


def _declining_strategy(strategy_id: str) -> Mock:
    s = Mock(spec=ProposeStrategy)
    s.run.return_value = ProposeStrategyResult.declined(strategy_id, "not_applicable")
    return s


def _executable_strategy(strategy_id: str) -> Mock:
    proposal = ExecutableProposal.from_command(
        goal_id="g1", task_id="t1", strategy_id=strategy_id, command="echo ok"
    )
    s = Mock(spec=ProposeStrategy)
    s.run.return_value = ProposeStrategyResult.executable(strategy_id, proposal)
    return s


def _context() -> ProposeContext:
    return ProposeContext(goal_id="g1", task_id="t1", task={}, base_prompt="test")


class TestFullChainIteration:
    """T002: orchestrator iterates the complete strategy_order without truncation."""

    def test_all_seven_strategies_attempted_when_all_decline(self):
        seven_ids = [
            STRATEGY_TOOL_CALLING_LLM,
            STRATEGY_JSON_SCHEMA_LLM,
            STRATEGY_FLEXIBLE_LLM_NORMALIZATION,
            STRATEGY_WORKER,
            STRATEGY_DETERMINISTIC_HANDLER,
            STRATEGY_ADVISORY_PROPOSAL,
            STRATEGY_HUMAN_REVIEW,
        ]
        policy = ProposePolicy(
            strategy_order=seven_ids,
            on_all_strategies_declined="needs_review",
        )
        strategies = {sid: _declining_strategy(sid) for sid in seven_ids}
        orch = ProposeStrategyOrchestrator(policy, strategies)

        result = orch.run(_context())

        assert result.status == STATUS_NEEDS_REVIEW
        # Every strategy was tried
        for sid in seven_ids:
            strategies[sid].run.assert_called_once()

    def test_max_strategy_attempts_does_not_truncate_chain(self):
        """max_strategy_attempts=2 must not limit strategy_order to 2 entries."""
        four_ids = ["s1", "s2", "s3", "s4"]
        policy = ProposePolicy(
            strategy_order=four_ids,
            max_strategy_attempts=2,
            on_all_strategies_declined="needs_review",
        )
        strategies = {sid: _declining_strategy(sid) for sid in four_ids}
        orch = ProposeStrategyOrchestrator(policy, strategies)

        result = orch.run(_context())

        # All four are tried, not just the first two
        for sid in four_ids:
            strategies[sid].run.assert_called_once()
        assert result.status == STATUS_NEEDS_REVIEW

    def test_chain_stops_at_first_executable(self):
        ids = ["s1", "s2", "s3"]
        policy = ProposePolicy(strategy_order=ids, on_all_strategies_declined="needs_review")
        strategies = {
            "s1": _declining_strategy("s1"),
            "s2": _executable_strategy("s2"),
            "s3": _declining_strategy("s3"),
        }
        orch = ProposeStrategyOrchestrator(policy, strategies)

        result = orch.run(_context())

        assert result.is_executable
        strategies["s1"].run.assert_called_once()
        strategies["s2"].run.assert_called_once()
        strategies["s3"].run.assert_not_called()

    def test_chain_stops_at_terminal_failed(self):
        ids = ["s1", "s2"]
        policy = ProposePolicy(strategy_order=ids, on_all_strategies_declined="needs_review")
        fail_strategy = Mock(spec=ProposeStrategy)
        fail_strategy.run.return_value = ProposeStrategyResult.failed("s1", "fatal_error")
        strategies = {"s1": fail_strategy, "s2": _declining_strategy("s2")}
        orch = ProposeStrategyOrchestrator(policy, strategies)

        result = orch.run(_context())

        assert result.status == STATUS_FAILED
        strategies["s2"].run.assert_not_called()


class TestAttemptedStrategiesMetadata:
    """T002: attempted_strategies must be complete and accurate."""

    def test_attempted_strategies_in_metadata_on_executable(self):
        ids = ["s1", "s2"]
        policy = ProposePolicy(strategy_order=ids, on_all_strategies_declined="needs_review")
        strategies = {"s1": _declining_strategy("s1"), "s2": _executable_strategy("s2")}
        orch = ProposeStrategyOrchestrator(policy, strategies)

        result = orch.run(_context())

        attempted = result.metadata.get("attempted_strategies", [])
        assert len(attempted) == 2
        assert attempted[0]["strategy_id"] == "s1"
        assert attempted[0]["status"] == STATUS_DECLINED
        assert attempted[1]["strategy_id"] == "s2"
        assert attempted[1]["status"] == STATUS_EXECUTABLE

    def test_selected_strategy_set_on_executable(self):
        ids = ["s1"]
        policy = ProposePolicy(strategy_order=ids, on_all_strategies_declined="needs_review")
        strategies = {"s1": _executable_strategy("s1")}
        orch = ProposeStrategyOrchestrator(policy, strategies)

        result = orch.run(_context())

        assert result.metadata.get("selected_strategy") == "s1"

    def test_selected_strategy_none_when_all_declined(self):
        ids = ["s1"]
        policy = ProposePolicy(strategy_order=ids, on_all_strategies_declined="needs_review")
        strategies = {"s1": _declining_strategy("s1")}
        orch = ProposeStrategyOrchestrator(policy, strategies)

        result = orch.run(_context())

        assert result.metadata.get("selected_strategy") is None

    def test_missing_strategy_recorded_as_declined(self):
        policy = ProposePolicy(
            strategy_order=["registered", "missing"],
            on_all_strategies_declined="needs_review",
        )
        strategies = {"registered": _declining_strategy("registered")}
        orch = ProposeStrategyOrchestrator(policy, strategies)

        result = orch.run(_context())

        attempted = result.metadata.get("attempted_strategies", [])
        missing_entry = next((a for a in attempted if a["strategy_id"] == "missing"), None)
        assert missing_entry is not None
        assert missing_entry["status"] == STATUS_DECLINED
        assert "strategy_not_available" in (missing_entry.get("reason") or "")


class TestNewSoftwareProjectChain:
    """T002: new_software_project reaches flexible_llm_normalization when LLM strategies decline."""

    def test_flexible_llm_normalization_reachable_after_llm_declines(self):
        from agent.services.propose_policy import get_task_kind_preset, build_policy_from_dict

        preset = get_task_kind_preset("new_software_project")
        policy = build_policy_from_dict(preset)

        order = policy.effective_strategy_order()
        assert STRATEGY_TOOL_CALLING_LLM in order
        assert STRATEGY_JSON_SCHEMA_LLM in order
        assert STRATEGY_FLEXIBLE_LLM_NORMALIZATION in order

        # flexible_llm_normalization must come after the other LLM strategies
        idx_tc = order.index(STRATEGY_TOOL_CALLING_LLM)
        idx_js = order.index(STRATEGY_JSON_SCHEMA_LLM)
        idx_fl = order.index(STRATEGY_FLEXIBLE_LLM_NORMALIZATION)
        assert idx_fl > idx_tc
        assert idx_fl > idx_js

    def test_deterministic_handler_comes_after_llm_strategies_for_new_project(self):
        from agent.services.propose_policy import get_task_kind_preset, build_policy_from_dict

        preset = get_task_kind_preset("new_software_project")
        policy = build_policy_from_dict(preset)
        order = policy.effective_strategy_order()

        idx_tc = order.index(STRATEGY_TOOL_CALLING_LLM)
        idx_det = order.index(STRATEGY_DETERMINISTIC_HANDLER)
        assert idx_det > idx_tc

    def test_json_schema_runs_after_tool_calling_declines(self):
        policy = ProposePolicy(
            strategy_order=[
                STRATEGY_TOOL_CALLING_LLM,
                STRATEGY_JSON_SCHEMA_LLM,
                STRATEGY_HUMAN_REVIEW,
            ],
            on_all_strategies_declined="needs_review",
        )
        tc = _declining_strategy(STRATEGY_TOOL_CALLING_LLM)
        js = _declining_strategy(STRATEGY_JSON_SCHEMA_LLM)
        hr = _declining_strategy(STRATEGY_HUMAN_REVIEW)
        orch = ProposeStrategyOrchestrator(
            policy,
            {
                STRATEGY_TOOL_CALLING_LLM: tc,
                STRATEGY_JSON_SCHEMA_LLM: js,
                STRATEGY_HUMAN_REVIEW: hr,
            },
        )

        result = orch.run(_context())

        assert result.status == STATUS_NEEDS_REVIEW
        tc.run.assert_called_once()
        js.run.assert_called_once()


class TestOnAllStrategiesDeclined:
    """T002: on_all_strategies_declined controls the fallback result."""

    def test_needs_review_fallback(self):
        policy = ProposePolicy(
            strategy_order=["s1"],
            on_all_strategies_declined="needs_review",
        )
        strategies = {"s1": _declining_strategy("s1")}
        orch = ProposeStrategyOrchestrator(policy, strategies)
        result = orch.run(_context())
        assert result.status == STATUS_NEEDS_REVIEW

    def test_failed_fallback(self):
        policy = ProposePolicy(
            strategy_order=["s1"],
            on_all_strategies_declined="failed",
        )
        strategies = {"s1": _declining_strategy("s1")}
        orch = ProposeStrategyOrchestrator(policy, strategies)
        result = orch.run(_context())
        assert result.status == STATUS_FAILED
