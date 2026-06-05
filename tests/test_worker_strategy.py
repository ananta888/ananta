"""Tests for WorkerStrategy — FA-T008."""

import pytest
from types import SimpleNamespace
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

    @staticmethod
    def _decision(**kwargs):
        defaults = dict(
            decision_status=SelectionDecisionStatus.no_eligible_worker,
            reason="no_suitable_workers",
            reason_codes=[],
            selected_worker=None,
            proposal_output=None,
            worker_output=None,
            output=None,
        )
        defaults.update(kwargs)
        return SimpleNamespace(**defaults)

    def test_declined_no_selection(self, context, monkeypatch):
        mock_service_instance = Mock()
        mock_service_instance.select.return_value = self._decision()

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
        mock_selected_worker = SimpleNamespace(worker_kind=SimpleNamespace(value="opencode"), worker_id="opencode-1")

        mock_service_instance = Mock()
        mock_service_instance.select.return_value = self._decision(
            decision_status=SelectionDecisionStatus.selected,
            reason="worker_selected",
            selected_worker=mock_selected_worker,
        )

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
        mock_selected_worker = SimpleNamespace(worker_kind=SimpleNamespace(value="hermes"), worker_id="hermes-1")

        mock_service_instance = Mock()
        mock_service_instance.select.return_value = self._decision(
            decision_status=SelectionDecisionStatus.selected,
            reason="worker_selected",
            selected_worker=mock_selected_worker,
        )

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
        mock_service_instance = Mock()
        mock_service_instance.select.return_value = self._decision(
            decision_status=SelectionDecisionStatus.selected,
            reason="worker_selected",
            selected_worker=SimpleNamespace(worker_id="worker-1"),
            proposal_output='{"tool_calls":[{"name":"write_file","args":{"path":"main.py","content":"print(1)"}}]}',
        )
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
        mock_service_instance = Mock()
        mock_service_instance.select.return_value = self._decision(
            decision_status=SelectionDecisionStatus.selected,
            reason="worker_selected",
            selected_worker=SimpleNamespace(worker_id="worker-2"),
            output="I suggest creating app.py and requirements.txt.",
        )
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
