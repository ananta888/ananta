"""Tests for LLMRepairStrategy — FA-T011."""

import pytest
from unittest.mock import Mock, patch

from worker.core.propose_orchestrator import ProposeContext
from agent.services.propose_strategies.llm_repair_strategy import LLMRepairStrategy
from worker.core.propose import (
    ProposeStrategyResult,
    ExecutableProposal,
    STATUS_DECLINED,
    STATUS_FAILED,
    STATUS_EXECUTABLE,
)


class TestLLMRepairStrategy:
    @pytest.fixture
    def context(self):
        ctx = Mock(ProposeContext)
        ctx.goal_id = "g1"
        ctx.task_id = "t1"
        ctx.tool_definitions_resolver.return_value = [{"name": "write_file"}]
        return ctx

    def test_repair_success_executable(self, context, monkeypatch):
        mock_repair_output = '{"tool_calls": [{"name": "write_file", "args": {}}]}'
        monkeypatch.setattr(
            "agent.services.propose_strategies.llm_repair_strategy.ModelInvocationService.invoke",
            lambda *args, **kwargs: mock_repair_output
        )
        mock_normalize = Mock()
        mock_proposal = ExecutableProposal.from_command(
            goal_id=context.goal_id,
            task_id=context.task_id,
            strategy_id="mock",
            command="echo mock"
        )
        mock_normalize.return_value = ProposeStrategyResult.executable("mock", mock_proposal)
        monkeypatch.setattr(
            "agent.services.propose_strategies.llm_repair_strategy.LLMResponseNormalizer",
            Mock(return_value=Mock(normalize=mock_normalize))
        )

        strategy = LLMRepairStrategy()
        result = strategy.run(context)

        assert result.status == STATUS_EXECUTABLE
        assert result.metadata["repair_attempted"] is True
        assert result.metadata["repair_success"] is True

    def test_repair_normalization_failed_declined(self, context, monkeypatch):
        mock_repair_output = "invalid text"
        monkeypatch.setattr(
            "agent.services.propose_strategies.llm_repair_strategy.ModelInvocationService.invoke",
            lambda *args, **kwargs: mock_repair_output
        )
        mock_normalize = Mock(return_value=ProposeStrategyResult.advisory("mock", "advisory"))

        monkeypatch.setattr(
            "agent.services.propose_strategies.llm_repair_strategy.LLMResponseNormalizer",
            Mock(return_value=Mock(normalize=mock_normalize))
        )

        strategy = LLMRepairStrategy()
        result = strategy.run(context)

        assert result.status == STATUS_DECLINED
        assert "repair_normalization_failed" in result.reason
        assert result.metadata["repair_success"] is False

    def test_repair_call_failed(self, context, monkeypatch):
        monkeypatch.setattr(
            "agent.services.propose_strategies.llm_repair_strategy.ModelInvocationService.invoke",
            Mock(side_effect=Exception("llm down"))
        )

        strategy = LLMRepairStrategy()
        result = strategy.run(context)

        assert result.status == STATUS_FAILED
        assert "repair_call_failed" in result.reason
        assert result.metadata["repair_attempted"] is True

    def test_repair_metadata_telemetry(self, context, monkeypatch):
        # Test repair_output_preview in metadata on fail
        mock_repair_output = "short preview test"
        monkeypatch.setattr(
            "agent.services.propose_strategies.llm_repair_strategy.ModelInvocationService.invoke",
            lambda *args, **kwargs: mock_repair_output
        )
        mock_normalize = Mock(return_value=ProposeStrategyResult.advisory("mock", "fail"))

        monkeypatch.setattr(
            "agent.services.propose_strategies.llm_repair_strategy.LLMResponseNormalizer",
            Mock(return_value=Mock(normalize=mock_normalize))
        )

        strategy = LLMRepairStrategy()
        result = strategy.run(context)

        assert "repair_output_preview" in result.metadata
        assert mock_repair_output[:200] in result.metadata["repair_output_preview"]
