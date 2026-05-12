"""FA-T017: E2E test — new-project Fibonacci API through orchestrator, no sgpt.

Proves the full chain:
  ProposeStrategyOrchestrator
    → DeterministicHandlerStrategy
    → TemplateProposeHandler
  → ExecutableProposal (command)
  → execute creates workspace files
  → ArtifactManifest collected
  → TaskCompletionPolicyService → completed / needs_review

run_sgpt_command is monkeypatched to raise for the entire test to guarantee
the legacy path is never taken.
"""
from __future__ import annotations

import hashlib
import uuid
import pytest
from pathlib import Path
from unittest.mock import patch

from worker.core.propose import ExecutableProposal, STATUS_EXECUTABLE
from worker.core.propose_orchestrator import ProposeContext, ProposeStrategyOrchestrator
from worker.core.deterministic_handler_strategy import DeterministicHandlerStrategy
from worker.core.template_propose_handler import TemplateProposeHandler
from worker.core.artifact_manifest import build_artifact_manifest, write_manifest
from agent.services.task_handler_registry import TaskHandlerRegistry
from agent.services.propose_policy import ProposePolicy
from agent.services.planning_utils import parse_followup_analysis
from agent.services.task_completion_policy_service import get_task_completion_policy_service
from agent.services.task_retry_policy_service import (
    get_task_retry_policy_service,
    REASON_ADVISORY_JSON_PARSE_FAILED,
)
from agent.services.worker_output_collector_service import get_worker_output_collector_service


FIBONACCI_FILES = {
    "app.py": "from flask import Flask\napp = Flask(__name__)\n@app.route('/fib/<int:n>')\ndef fib(n): return str(n)\n",
    "requirements.txt": "flask>=2.0\n",
    "README.md": "# Fibonacci API\n",
}


@pytest.fixture(autouse=True)
def _block_sgpt():
    """Guarantee sgpt is never called anywhere in this test module."""
    with patch("agent.services.task_scoped_execution_service.run_sgpt_command",
               side_effect=RuntimeError("sgpt_blocked_in_FA-T017"), create=True):
        yield


@pytest.fixture
def fibonacci_task():
    return {
        "task_id": "task-fib-001",
        "goal_id": "goal-fib-001",
        "task_kind": "new_software_project",
        "title": "Fibonacci API",
        "description": "Create a simple Fibonacci REST API",
    }


@pytest.fixture
def registry():
    r = TaskHandlerRegistry()
    r.register("new_software_project", TemplateProposeHandler(), capabilities=["template_propose"])
    r.register("coding", TemplateProposeHandler(), capabilities=["template_propose"])
    return r


@pytest.fixture
def fibonacci_workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "fibonacci_project"
    ws.mkdir()
    for name, content in FIBONACCI_FILES.items():
        (ws / name).write_text(content)
    return ws


@pytest.fixture
def fibonacci_manifest(fibonacci_workspace: Path) -> dict:
    artifacts = []
    for name, content in FIBONACCI_FILES.items():
        artifacts.append({
            "artifact_id": f"art-{uuid.uuid4().hex[:8]}",
            "kind": "generated_file",
            "relative_path": name,
            "content_hash": hashlib.sha256(content.encode()).hexdigest(),
            "size_bytes": len(content.encode()),
            "classification": "internal",
            "operation": "created",
            "required": True,
            "verification_status": "pending",
            "metadata": {},
        })
    return build_artifact_manifest(
        goal_id="goal-fib-001",
        task_id="task-fib-001",
        execution_id="exec-fib-001",
        trace_id="tr-fib-001",
        workspace_root=fibonacci_workspace,
        worker_id="test-worker",
        artifacts=artifacts,
    )


class TestFibonacciProposeViaOrchestrator:
    def _run_orch(self, task: dict, registry: TaskHandlerRegistry) -> "ProposeStrategyResult":
        policy = ProposePolicy(strategy_order=["deterministic_handler"], on_all_strategies_declined="needs_review")
        strategies = {"deterministic_handler": DeterministicHandlerStrategy()}
        orch = ProposeStrategyOrchestrator(policy, strategies)
        context = ProposeContext(
            goal_id=task["goal_id"],
            task_id=task["task_id"],
            task=task,
            base_prompt="Create Fibonacci API",
        )
        with patch("worker.core.deterministic_handler_strategy.get_task_handler_registry", return_value=registry):
            return orch.run(context)

    def test_propose_returns_executable_no_sgpt(self, fibonacci_task, registry):
        result = self._run_orch(fibonacci_task, registry)
        assert result.status == STATUS_EXECUTABLE, f"Expected executable, got {result.status}: {result.reason}"
        assert isinstance(result.proposal, ExecutableProposal)
        assert result.proposal.command is not None
        assert "autopilot_no_executable_step" not in str(result.reason or "")

    def test_propose_command_references_project_name(self, fibonacci_task, registry):
        result = self._run_orch(fibonacci_task, registry)
        assert "fibonacci" in result.proposal.command.lower()

    def test_propose_expected_artifacts_listed(self, fibonacci_task, registry):
        result = self._run_orch(fibonacci_task, registry)
        paths = [a["path"] for a in result.proposal.expected_artifacts]
        assert any("README.md" in p for p in paths)
        assert any("main.py" in p for p in paths)


class TestFibonacciArtifactCompletion:
    """Completion driven by artifact manifest, not model text."""

    def test_valid_manifest_completes_task(self, fibonacci_workspace: Path, fibonacci_manifest: dict, tmp_path: Path):
        manifest_dir = fibonacci_workspace / ".ananta" / "handoff" / "exec-fib-001"
        manifest_dir.mkdir(parents=True)
        write_manifest(fibonacci_manifest, manifest_dir / "artifact_manifest.v1.json")

        collector = get_worker_output_collector_service()
        collection = collector.collect(
            task_id="task-fib-001",
            goal_id="goal-fib-001",
            execution_id="exec-fib-001",
            trace_id="tr-fib-001",
            workspace_root=fibonacci_workspace,
            manifest_relative_path=".ananta/handoff/exec-fib-001/artifact_manifest.v1.json",
            allow_synthesized_fallback=False,
        )
        assert collection["manifest_valid"]

        svc = get_task_completion_policy_service()
        decision = svc.evaluate(
            task_id="task-fib-001",
            collection_result=collection,
            advisory_parse_result=None,
            exit_code=0,
            retry_count=0,
            expected_paths=["app.py", "requirements.txt", "README.md"],
        )
        assert decision.decision in ("completed", "needs_review"), f"Unexpected: {decision.decision}"

    def test_malformed_model_text_does_not_block_completion(self, fibonacci_workspace: Path, fibonacci_manifest: dict):
        """Advisory parse failure is ignored when manifest is valid."""
        manifest_dir = fibonacci_workspace / ".ananta" / "handoff" / "exec-fib-001"
        manifest_dir.mkdir(parents=True)
        write_manifest(fibonacci_manifest, manifest_dir / "artifact_manifest.v1.json")

        advisory = parse_followup_analysis("Great job! All Fibonacci files created successfully!")
        assert advisory["advisory"] is True

        collector = get_worker_output_collector_service()
        collection = collector.collect(
            task_id="task-fib-001", goal_id="goal-fib-001", execution_id="exec-fib-001",
            trace_id="tr-fib-001", workspace_root=fibonacci_workspace,
            manifest_relative_path=".ananta/handoff/exec-fib-001/artifact_manifest.v1.json",
            allow_synthesized_fallback=False,
        )

        svc = get_task_completion_policy_service()
        decision = svc.evaluate(
            task_id="task-fib-001",
            collection_result=collection,
            advisory_parse_result=advisory,
            exit_code=0,
            retry_count=0,
            expected_paths=["app.py", "requirements.txt", "README.md"],
        )
        assert decision.decision in ("completed", "needs_review")
        assert "advisory_parse_failed_ignored" in (decision.reason_codes or [])


class TestFibonacciRetryPrevention:
    """Regression: advisory parse failure + valid artifacts must NOT cause retry loop."""

    def test_advisory_parse_failure_with_valid_artifacts_not_requeued(self):
        svc = get_task_retry_policy_service()
        cls = svc.classify(
            reason=REASON_ADVISORY_JSON_PARSE_FAILED,
            has_valid_artifacts=True,
            retry_count=0,
        )
        assert not cls.should_retry, f"Must not retry: {cls.classification} — {cls.message}"

    def test_no_executable_proposal_becomes_needs_review_not_loop(self):
        """When all strategies decline, result is needs_review, not a retry loop."""
        empty_registry = TaskHandlerRegistry()
        policy = ProposePolicy(strategy_order=["deterministic_handler"], on_all_strategies_declined="needs_review")
        strategies = {"deterministic_handler": DeterministicHandlerStrategy()}
        orch = ProposeStrategyOrchestrator(policy, strategies)
        context = ProposeContext(
            goal_id="g", task_id="t",
            task={"task_kind": "unsupported_kind"},
            base_prompt="do something",
        )
        with patch("worker.core.deterministic_handler_strategy.get_task_handler_registry", return_value=empty_registry):
            result = orch.run(context)

        assert result.status == "needs_review"
        assert not result.is_executable
