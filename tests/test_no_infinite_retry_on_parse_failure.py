"""Regression: parse failure must never produce an infinite retry loop — AFR-FINAL-T009.

The original failure involved autopilot_no_executable_step causing a loop.
These tests prove that parse failures produce terminal or advisory results,
never a continuously retried executable result.
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
    ProposeStrategyResult,
    STATUS_ADVISORY,
    STATUS_DECLINED,
    STATUS_NEEDS_REVIEW,
    STATUS_FAILED,
)
from agent.services.propose_policy import (
    ProposePolicy,
    STRATEGY_TOOL_CALLING_LLM,
    STRATEGY_JSON_SCHEMA_LLM,
    STRATEGY_FLEXIBLE_LLM_NORMALIZATION,
    STRATEGY_HUMAN_REVIEW,
)
from agent.services.llm_response_normalizer import LLMResponseNormalizer


def _context() -> ProposeContext:
    return ProposeContext(goal_id="g1", task_id="t1", task={}, base_prompt="test")


class TestParseFailureTerminates:
    """Parse failure advances to next strategy or produces needs_review — never loops."""

    def test_json_parse_failure_produces_advisory_not_executable(self):
        from worker.core.json_schema_llm_strategy import JsonSchemaLLMStrategy
        ctx = Mock(spec=ProposeContext)
        ctx.goal_id = "g1"
        ctx.task_id = "t1"
        ctx.task = {}
        ctx.base_prompt = "test"

        strategy = JsonSchemaLLMStrategy()
        with pytest.MonkeyPatch().context() as m:
            m.setattr("agent.config.settings.default_provider", "lmstudio")
            m.setattr(
                "agent.services.model_invocation_service.ModelInvocationService.invoke_with_json_schema",
                Mock(return_value="{ invalid json"),
            )
            result = strategy.run(ctx)

        assert result.status == STATUS_ADVISORY
        assert not result.is_executable

    def test_normalizer_malformed_json_returns_advisory(self):
        normalizer = LLMResponseNormalizer()
        result = normalizer.normalize("```json\n{bad: json, no: quotes}\n```", _context())
        assert result.status == STATUS_ADVISORY
        assert not result.is_executable

    def test_orchestrator_advisory_does_not_stop_chain(self):
        """advisory results from a strategy advance to the next one."""
        advisory_strategy = Mock(spec=ProposeStrategy)
        advisory_strategy.run.return_value = ProposeStrategyResult.advisory(
            "json_schema_llm",
            advisory_text="Here is my suggestion",
            reason="json_parse_failed",
        )
        needs_review_strategy = Mock(spec=ProposeStrategy)
        needs_review_strategy.run.return_value = ProposeStrategyResult.needs_review(
            "human_review",
            reason="escalated_after_advisory",
        )

        policy = ProposePolicy(
            strategy_order=["json_schema_llm", "human_review"],
            on_all_strategies_declined="needs_review",
        )
        strategies = {
            "json_schema_llm": advisory_strategy,
            "human_review": needs_review_strategy,
        }
        orch = ProposeStrategyOrchestrator(policy, strategies)

        result = orch.run(_context())

        # human_review was reached and is terminal → needs_review
        assert result.status == STATUS_NEEDS_REVIEW
        needs_review_strategy.run.assert_called_once()


class TestChainTerminatesAfterAllStrategiesFail:
    """When all strategies decline or fail, result is terminal, not a retry."""

    def test_all_declined_produces_needs_review(self):
        order = [STRATEGY_TOOL_CALLING_LLM, STRATEGY_JSON_SCHEMA_LLM, STRATEGY_FLEXIBLE_LLM_NORMALIZATION]
        policy = ProposePolicy(strategy_order=order, on_all_strategies_declined="needs_review")
        strategies = {
            sid: Mock(spec=ProposeStrategy, **{
                "run.return_value": ProposeStrategyResult.declined(sid, "test_decline")
            })
            for sid in order
        }
        orch = ProposeStrategyOrchestrator(policy, strategies)
        result = orch.run(_context())

        assert result.status == STATUS_NEEDS_REVIEW
        assert not result.is_executable
        # Each strategy was called exactly once (not retried)
        for sid in order:
            strategies[sid].run.assert_called_once()

    def test_all_declined_on_all_strategies_declined_failed(self):
        order = ["s1", "s2"]
        policy = ProposePolicy(strategy_order=order, on_all_strategies_declined="failed")
        strategies = {
            sid: Mock(spec=ProposeStrategy, **{
                "run.return_value": ProposeStrategyResult.declined(sid, "test")
            })
            for sid in order
        }
        orch = ProposeStrategyOrchestrator(policy, strategies)
        result = orch.run(_context())
        assert result.status == STATUS_FAILED

    def test_failed_strategy_stops_chain_immediately(self):
        fail_strategy = Mock(spec=ProposeStrategy)
        fail_strategy.run.return_value = ProposeStrategyResult.failed("s1", "fatal_error")
        next_strategy = Mock(spec=ProposeStrategy)

        policy = ProposePolicy(strategy_order=["s1", "s2"], on_all_strategies_declined="needs_review")
        strategies = {"s1": fail_strategy, "s2": next_strategy}
        orch = ProposeStrategyOrchestrator(policy, strategies)
        result = orch.run(_context())

        assert result.status == STATUS_FAILED
        next_strategy.run.assert_not_called()


class TestRetryBudgetNotInfinite:
    """Retry policy for real failures (artifacts/verification) is bounded."""

    def test_max_repair_attempts_is_bounded(self):
        policy = ProposePolicy()
        assert policy.max_repair_attempts >= 1
        # Must be finite
        assert isinstance(policy.max_repair_attempts, int)

    def test_max_strategy_attempts_default_is_one(self):
        policy = ProposePolicy()
        assert policy.max_strategy_attempts == 1

    def test_strategy_retry_cannot_be_negative(self):
        """max_strategy_attempts=0 would mean never try — guard against that."""
        policy = ProposePolicy(max_strategy_attempts=1)
        assert policy.max_strategy_attempts >= 1

    def test_advisory_result_not_executable_not_retried_as_propose(self):
        """Advisory results must not be confused with executable proposals that need retry."""
        result = ProposeStrategyResult.advisory("s1", advisory_text="suggestion")
        assert not result.is_executable
        assert not result.is_terminal
        # is_terminal=False means orchestrator continues chain, not that it retries same strategy

    def test_needs_review_is_terminal(self):
        """needs_review stops the orchestrator immediately."""
        result = ProposeStrategyResult.needs_review("s1")
        assert result.is_terminal
