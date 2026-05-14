"""Tests for WorkerStrategy — FA-T008."""

import pytest
from unittest.mock import Mock

from worker.core.propose_orchestrator import ProposeContext
from worker.core.propose import STATUS_ADVISORY, STATUS_EXECUTABLE, STATUS_DECLINED
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

    def test_selected_worker_with_structured_output_becomes_executable(self, context, monkeypatch):
        mock_selected_worker = Mock()
        mock_selected_worker.worker_id = "worker-1"
        mock_decision = Mock()
        mock_decision.status = SelectionDecisionStatus.selected
        mock_decision.selected_worker = mock_selected_worker
        mock_decision.proposal_output = '{"tool_calls":[{"name":"write_file","args":{"path":"main.py","content":"print(1)"}}]}'

        mock_service_instance = Mock()
        mock_service_instance.select.return_value = mock_decision
        monkeypatch.setattr(
            "worker.core.worker_strategy.WorkerRuntimeSelectionService",
            Mock(return_value=mock_service_instance),
        )

        strategy = WorkerStrategy()
        result = strategy.run(context)
        assert result.status == STATUS_EXECUTABLE
        assert result.proposal.tool_calls[0]["name"] == "write_file"
        assert result.metadata["source"] == "worker_strategy_output"

    def test_selected_worker_with_prose_output_stays_advisory(self, context, monkeypatch):
        mock_decision = Mock()
        mock_decision.status = SelectionDecisionStatus.selected
        mock_decision.selected_worker = Mock(worker_id="worker-2")
        mock_decision.output = "I suggest creating app.py and requirements.txt."

        mock_service_instance = Mock()
        mock_service_instance.select.return_value = mock_decision
        monkeypatch.setattr(
            "worker.core.worker_strategy.WorkerRuntimeSelectionService",
            Mock(return_value=mock_service_instance),
        )

        strategy = WorkerStrategy()
        result = strategy.run(context)
        assert result.status == STATUS_ADVISORY

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
