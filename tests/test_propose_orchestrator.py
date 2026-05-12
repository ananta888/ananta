"""Tests for ProposeStrategyOrchestrator per FA-T005."""
from unittest.mock import Mock, MagicMock

import pytest

from worker.core.propose_orchestrator import (
    ProposeContext,
    ProposeStrategyOrchestrator,
    ProposeStrategy,
)

from worker.core.propose import (
    ProposeStrategyResult,
    ExecutableProposal,
    STATUS_EXECUTABLE,
    STATUS_DECLINED,
    STATUS_NEEDS_REVIEW,
)

from agent.services.propose_policy import ProposePolicy


class TestProposeStrategyOrchestrator:
    @pytest.fixture
    def context(self):
        return ProposeContext(
            goal_id="g1",
            task_id="t1",
            task={},
            base_prompt="test",
        )

    @pytest.fixture
    def mock_strategy(self):
        strategy = Mock(spec=ProposeStrategy)
        strategy.run.return_value = None  # default
        return strategy

    @pytest.fixture
    def mock_policy(self):
        policy = Mock(spec=ProposePolicy)
        policy.effective_strategy_order.return_value = ["strat1", "strat2"]
        policy.max_strategy_attempts = 2
        policy.on_all_strategies_declined = "needs_review"
        return policy

    def test_runs_strategies_in_order_until_executable_or_terminal(self, context, mock_policy, mock_strategy):
        executable = ExecutableProposal.from_command(
            goal_id="g1", task_id="t1", strategy_id="strat1", command="echo test"
        )
        mock_strategy1 = Mock()
        mock_strategy1.run.return_value = ProposeStrategyResult.executable("strat1", executable)
        mock_strategy2 = Mock()
        mock_strategy2.run.return_value = ProposeStrategyResult.declined("strat2")
        strategies = {"strat1": mock_strategy1, "strat2": mock_strategy2}

        orch = ProposeStrategyOrchestrator(policy=mock_policy, strategies=strategies)
        result = orch.run(context)

        assert result.is_executable
        mock_policy.effective_strategy_order.assert_called_once()
        mock_strategy1.run.assert_called_once_with(context)
        mock_strategy2.run.assert_not_called()

    def test_all_declined_to_needs_review(self, context, mock_policy, mock_strategy):
        mock_policy.on_all_strategies_declined = "needs_review"
        mock_strategy.run.return_value = ProposeStrategyResult.declined("strat1")
        strategies = {"strat1": mock_strategy, "strat2": mock_strategy}

        orch = ProposeStrategyOrchestrator(policy=mock_policy, strategies=strategies)
        result = orch.run(context)

        assert result.status == STATUS_NEEDS_REVIEW
        assert "all_strategies_declined" in result.reason

    def test_terminal_early_return(self, context, mock_policy, mock_strategy):
        mock_strategy.run.return_value = ProposeStrategyResult.failed("strat1")
        strategies = {"strat1": mock_strategy}

        orch = ProposeStrategyOrchestrator(policy=mock_policy, strategies=strategies)
        result = orch.run(context)

        assert result.status == "failed"

    def test_missing_strategy_declined(self, context, mock_policy):
        mock_policy.effective_strategy_order.return_value = ["missing"]
        strategies = {}

        orch = ProposeStrategyOrchestrator(policy=mock_policy, strategies=strategies)
        result = orch.run(context)

        assert result.status == STATUS_NEEDS_REVIEW
        assert result.strategy_id == "orchestrator"
        assert "all_strategies_declined" in (result.reason or "")

    def test_max_attempts_limits(self, context, mock_policy):
        mock_policy.effective_strategy_order.return_value = ["s1", "s2", "s3"]
        mock_policy.max_strategy_attempts = 2
        strategies = {f"s{i}": Mock() for i in "123"}

        orch = ProposeStrategyOrchestrator(policy=mock_policy, strategies=strategies)
        orch.run(context)

        strategies["s3"].run.assert_not_called()