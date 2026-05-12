"""FA-T017: Unit tests for TaskScopedExecutionService.propose_task_step via orchestrator."""
from __future__ import annotations

import pytest
from unittest.mock import Mock, patch, MagicMock

from worker.core.propose import (
    ExecutableProposal,
    ProposeStrategyResult,
    STATUS_EXECUTABLE,
    STATUS_DECLINED,
)
from worker.core.propose_orchestrator import ProposeContext, ProposeStrategyOrchestrator
from worker.core.deterministic_handler_strategy import DeterministicHandlerStrategy
from worker.core.template_propose_handler import TemplateProposeHandler
from agent.services.task_handler_registry import TaskHandlerRegistry
from agent.services.propose_policy import ProposePolicy


class TestDeterministicHandlerStrategyWithTemplate:
    """DeterministicHandlerStrategy correctly wraps TemplateProposeHandler."""

    def _make_registry(self, task_kind: str) -> TaskHandlerRegistry:
        registry = TaskHandlerRegistry()
        registry.register(
            task_kind,
            TemplateProposeHandler(),
            capabilities=["template_propose"],
            safety_flags={"requires_review": False},
        )
        return registry

    def _make_context(self, task_kind: str) -> ProposeContext:
        return ProposeContext(
            goal_id="goal-fib",
            task_id="task-001",
            task={"task_kind": task_kind, "title": "Fibonacci API", "goal_id": "goal-fib"},
            base_prompt="Create a Fibonacci API",
        )

    def test_new_software_project_returns_executable(self):
        registry = self._make_registry("new_software_project")
        strategy = DeterministicHandlerStrategy()
        with patch("worker.core.deterministic_handler_strategy.get_task_handler_registry", return_value=registry):
            context = self._make_context("new_software_project")
            result = strategy.run(context)
        assert result.status == STATUS_EXECUTABLE
        assert isinstance(result.proposal, ExecutableProposal)
        assert result.proposal.command is not None
        assert "fibonacci-api" in result.proposal.command.lower() or "mkdir" in result.proposal.command.lower()

    def test_coding_task_kind_returns_executable(self):
        registry = self._make_registry("coding")
        strategy = DeterministicHandlerStrategy()
        with patch("worker.core.deterministic_handler_strategy.get_task_handler_registry", return_value=registry):
            context = self._make_context("coding")
            result = strategy.run(context)
        assert result.status == STATUS_EXECUTABLE
        assert result.proposal.command is not None

    def test_unknown_kind_returns_declined(self):
        registry = TaskHandlerRegistry()  # empty
        strategy = DeterministicHandlerStrategy()
        with patch("worker.core.deterministic_handler_strategy.get_task_handler_registry", return_value=registry):
            context = self._make_context("unknown_kind")
            result = strategy.run(context)
        assert result.status == STATUS_DECLINED
        assert "no_suitable_handler" in (result.reason or "")

    def test_sgpt_not_called(self):
        """Prove sgpt is never called when the handler resolves.
        run_sgpt_command is not imported into task_scoped_execution_service — that absence
        is itself the proof that the legacy path is removed (FA-T003). Here we verify the
        strategy result is executable without any sgpt call.
        """
        registry = self._make_registry("new_software_project")
        strategy = DeterministicHandlerStrategy()
        # Patch the sgpt module itself to blow up if touched.
        with patch("agent.routes.sgpt.run_sgpt_command", side_effect=RuntimeError("sgpt_must_not_be_called"), create=True):
            with patch("worker.core.deterministic_handler_strategy.get_task_handler_registry", return_value=registry):
                context = self._make_context("new_software_project")
                result = strategy.run(context)
        assert result.status == STATUS_EXECUTABLE


class TestOrchestratorWithTemplateProposeHandler:
    """Orchestrator stops at deterministic_handler when it returns executable."""

    def _make_policy(self) -> ProposePolicy:
        return ProposePolicy(
            strategy_order=[
                "deterministic_handler",
                "worker_strategy",
                "tool_calling_llm",
                "flexible_llm_normalization",
            ],
            on_all_strategies_declined="needs_review",
        )

    def test_orchestrator_stops_at_deterministic(self):
        registry = TaskHandlerRegistry()
        registry.register(
            "new_software_project",
            TemplateProposeHandler(),
            capabilities=["template_propose"],
        )
        det_strategy = DeterministicHandlerStrategy()
        fallback = Mock()
        fallback.run.side_effect = RuntimeError("fallback_must_not_run")

        strategies = {
            "deterministic_handler": det_strategy,
            "worker_strategy": fallback,
            "tool_calling_llm": fallback,
            "flexible_llm_normalization": fallback,
        }
        policy = self._make_policy()
        orch = ProposeStrategyOrchestrator(policy, strategies)
        context = ProposeContext(
            goal_id="goal-fib",
            task_id="task-001",
            task={"task_kind": "new_software_project", "title": "Fibonacci API", "goal_id": "goal-fib"},
            base_prompt="Create Fibonacci API",
        )

        with patch("worker.core.deterministic_handler_strategy.get_task_handler_registry", return_value=registry):
            result = orch.run(context)

        assert result.status == STATUS_EXECUTABLE
        fallback.run.assert_not_called()

    def test_orchestrator_no_executable_step_becomes_needs_review(self):
        """All strategies decline → needs_review, not infinite retry."""
        empty_registry = TaskHandlerRegistry()
        det_strategy = DeterministicHandlerStrategy()
        strategies = {"deterministic_handler": det_strategy}
        policy = ProposePolicy(
            strategy_order=["deterministic_handler"],
            on_all_strategies_declined="needs_review",
        )
        orch = ProposeStrategyOrchestrator(policy, strategies)
        context = ProposeContext(
            goal_id="g",
            task_id="t",
            task={"task_kind": "unknown_unsupported"},
            base_prompt="foo",
        )
        with patch("worker.core.deterministic_handler_strategy.get_task_handler_registry", return_value=empty_registry):
            result = orch.run(context)
        assert result.status == "needs_review"

    def test_template_handler_expected_artifacts_present(self):
        """ExecutableProposal from TemplateProposeHandler must list expected artifacts."""
        registry = TaskHandlerRegistry()
        registry.register(
            "new_software_project",
            TemplateProposeHandler(),
            capabilities=["template_propose"],
        )
        strategy = DeterministicHandlerStrategy()
        context = ProposeContext(
            goal_id="g",
            task_id="t",
            task={"task_kind": "new_software_project", "title": "My Project", "goal_id": "g"},
            base_prompt="create project",
        )
        with patch("worker.core.deterministic_handler_strategy.get_task_handler_registry", return_value=registry):
            result = strategy.run(context)
        assert result.status == STATUS_EXECUTABLE
        assert len(result.proposal.expected_artifacts) >= 2
        kinds = {a["kind"] for a in result.proposal.expected_artifacts}
        assert "file" in kinds
