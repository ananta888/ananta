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

    def test_declined_when_worker_selected_but_delegation_not_implemented(self, context, monkeypatch):
        """When a worker is selected, real delegation is TODO → declined with diagnostics."""
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

        assert result.status == STATUS_DECLINED
        assert "real_worker_delegation_not_implemented" in result.reason

    def test_declined_hermes_worker_delegation_not_implemented(self, context, monkeypatch):
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

        assert result.status == STATUS_DECLINED
        assert "real_worker_delegation_not_implemented" in result.reason

    def test_declined_selection_service_exception(self, context, monkeypatch):
        mock_service_instance = Mock()
        mock_service_instance.select.side_effect = Exception("service error")

        mock_service_cls = Mock(return_value=mock_service_instance)
        monkeypatch.setattr(
            "worker.core.worker_strategy.WorkerRuntimeSelectionService",
            mock_service_cls,
        )

        strategy = WorkerStrategy()
        result = strategy.run(context)

        # Exception in selection service → declined with diagnostics (not failed)
        assert result.status == STATUS_DECLINED
        assert "worker_strategy_error" in result.reason
