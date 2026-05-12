"""Tests for WorkerStrategy — FA-T008."""

import pytest
from unittest.mock import Mock, MagicMock

from worker.core.propose_orchestrator import ProposeContext
from worker.core.propose import (
    ProposeStrategyResult,
    ExecutableProposal,
    STATUS_ADVISORY,
    STATUS_EXECUTABLE,
    STATUS_DECLINED,
    STATUS_FAILED,
)
from worker.core.runtime_target import SelectionDecisionStatus

from worker.core.worker_strategy import WorkerStrategy


class TestWorkerStrategy:
    @pytest.fixture
    def context(self):
        return ProposeContext(
            goal_id="test-goal-1",
            task_id="test-task-1",
            task={"kind": "coding/new_software_project"},
            base_prompt="Propose for Fibonacci API.",
        )

    @pytest.fixture
    def mock_propose_strategy_result(self):
        result = Mock(spec=ProposeStrategyResult)
        result.is_executable = False
        result.is_terminal = False
        return result

    def test_declined_no_selection(self, context, monkeypatch):
        mock_decision = Mock()
        mock_decision.status = SelectionDecisionStatus.no_eligible_worker
        mock_decision.reason = "no_suitable_workers"
        mock_decision.reason_codes = []

        mock_service_instance = Mock()
        mock_service_instance.select.return_value = mock_decision

        mock_service_cls = Mock(return_value=mock_service_instance)
        monkeypatch.setattr(
            "worker.core.worker_strategy.WorkerRuntimeSelectionService",
            mock_service_cls,
        )

        strategy = WorkerStrategy()
        result = strategy.run(context)

        assert result.status == STATUS_DECLINED
        assert "no_suitable_workers" in result.reason

    def test_advisory_hermes_free_text(self, context, monkeypatch):
        mock_selected_worker = Mock()
        mock_selected_worker.worker_kind.value = "hermes"
        mock_selected_worker.worker_id = "hermes-1"

        mock_decision = Mock()
        mock_decision.status = SelectionDecisionStatus.selected
        mock_decision.selected_worker = mock_selected_worker

        mock_service_instance = Mock()
        mock_service_instance.select.return_value = mock_decision

        mock_service_cls = Mock(return_value=mock_service_instance)
        monkeypatch.setattr(
            "worker.core.worker_strategy.WorkerRuntimeSelectionService",
            mock_service_cls,
        )

        strategy = WorkerStrategy()
        result = strategy.run(context)

        assert result.status == STATUS_ADVISORY
        assert "Hermes worker generated advisory proposal text." in result.advisory_text

    def test_executable_opencode_tool_calls(self, context, monkeypatch):
        mock_selected_worker = Mock()
        mock_selected_worker.worker_kind.value = "opencode"
        mock_selected_worker.worker_id = "opencode-1"

        mock_decision = Mock()
        mock_decision.status = SelectionDecisionStatus.selected
        mock_decision.selected_worker = mock_selected_worker

        mock_service_instance = Mock()
        mock_service_instance.select.return_value = mock_decision

        mock_service_cls = Mock(return_value=mock_service_instance)
        monkeypatch.setattr(
            "worker.core.worker_strategy.WorkerRuntimeSelectionService",
            mock_service_cls,
        )

        strategy = WorkerStrategy()
        result = strategy.run(context)

        assert result.status == STATUS_EXECUTABLE
        proposal = result.proposal
        assert isinstance(proposal, ExecutableProposal)
        assert proposal.strategy_id == "worker_strategy"
        assert proposal.tool_calls  # has tool_calls
        assert proposal.metadata["worker_kind"] == "opencode"

    def test_executable_native_command(self, context, monkeypatch):
        mock_selected_worker = Mock()
        mock_selected_worker.worker_kind.value = "native"
        mock_selected_worker.worker_id = "native-1"

        mock_decision = Mock()
        mock_decision.status = SelectionDecisionStatus.selected
        mock_decision.selected_worker = mock_selected_worker

        mock_service_instance = Mock()
        mock_service_instance.select.return_value = mock_decision

        mock_service_cls = Mock(return_value=mock_service_instance)
        monkeypatch.setattr(
            "worker.core.worker_strategy.WorkerRuntimeSelectionService",
            mock_service_cls,
        )

        strategy = WorkerStrategy()
        result = strategy.run(context)

        assert result.status == STATUS_EXECUTABLE
        proposal = result.proposal
        assert proposal.command == "echo 'Native worker propose complete.'"

    def test_failed_normalization(self, context, monkeypatch):
        # Mock to raise ValueError in ExecutableProposal
        mock_selected_worker = Mock()
        mock_selected_worker.worker_kind.value = "invalid"
        mock_selected_worker.worker_id = "invalid-1"

        mock_decision = Mock()
        mock_decision.status = SelectionDecisionStatus.selected
        mock_decision.selected_worker = mock_selected_worker
        mock_decision.reason = None

        mock_service_instance = Mock()
        mock_service_instance.select.return_value = mock_decision

        mock_service_cls = Mock(return_value=mock_service_instance)
        monkeypatch.setattr(
            "worker.core.worker_strategy.WorkerRuntimeSelectionService",
            mock_service_cls,
        )

        # Monkeypatch ExecutableProposal to raise
        def mock_exec_proposal(**kwargs):
            raise ValueError("invalid fields")

        monkeypatch.setattr("worker.core.worker_strategy.ExecutableProposal", mock_exec_proposal)

        strategy = WorkerStrategy()
        result = strategy.run(context)

        assert result.status == STATUS_FAILED
        assert "normalization_failed" in result.reason

    def test_failed_selection_exception(self, context, monkeypatch):
        mock_service_instance = Mock()
        mock_service_instance.select.side_effect = Exception("service error")

        mock_service_cls = Mock(return_value=mock_service_instance)
        monkeypatch.setattr(
            "worker.core.worker_strategy.WorkerRuntimeSelectionService",
            mock_service_cls,
        )

        strategy = WorkerStrategy()
        result = strategy.run(context)

        assert result.status == STATUS_FAILED
        assert "worker_strategy_error" in result.reason
