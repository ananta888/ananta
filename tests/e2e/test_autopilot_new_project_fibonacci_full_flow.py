"""AFF-E2E-T001..T003: Full-flow Autopilot E2E — new-project Fibonacci via LLM-first path.

Proves the complete Autopilot lifecycle end-to-end:
  T001 — propose (LLM-first) → execute_local_step → files physically on disk
  T002 — artifact collection via WorkerOutputCollectorService (synthesized workspace diff)
  T003 — TaskCompletionPolicyService returns COMPLETED from artifact evidence

sgpt is blocked. No real LLM is contacted. Execution uses a temporary isolated workspace.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from agent.models import TaskExecutionPolicyContract
from agent.services.task_execution_service import TaskExecutionService
from agent.services.task_completion_policy_service import get_task_completion_policy_service
from agent.services.worker_output_collector_service import get_worker_output_collector_service
from agent.services.workspace_diff_service import WorkspaceDiffService
from agent.services.propose_policy import (
    STRATEGY_TOOL_CALLING_LLM,
    build_policy_from_dict,
    get_task_kind_preset,
)
from agent.services.propose_strategy_registry import build_strategy_registry
from worker.core.propose import STATUS_EXECUTABLE
from worker.core.propose_orchestrator import ProposeContext, ProposeStrategyOrchestrator

from tests.fixtures.mock_openai_compatible_provider import make_mock_invoke_with_tools


GOAL_ID = "goal-fib-full-e2e-001"
TASK_ID = "task-fib-full-e2e-001"
EXECUTION_ID = "exec-fib-full-e2e-001"
TRACE_ID = "trace-fib-full-e2e-001"

EXPECTED_RELATIVE_PATHS = ["app.py", "requirements.txt", "README.md", "tests/test_app.py"]

_FIBONACCI_CONTENT = {
    "app.py": (
        "from flask import Flask\n"
        "app = Flask(__name__)\n\n"
        "def fibonacci(n):\n"
        "    a, b = 0, 1\n"
        "    for _ in range(n):\n"
        "        a, b = b, a + b\n"
        "    return a\n\n"
        "@app.route('/fib/<int:n>')\n"
        "def fib(n):\n"
        "    return str(fibonacci(n))\n\n"
        "if __name__ == '__main__':\n"
        "    app.run()\n"
    ),
    "requirements.txt": "flask>=2.0\npytest>=7.0\n",
    "README.md": "# Fibonacci API\nA simple Fibonacci REST API.\n",
    "tests/test_app.py": (
        "from app import fibonacci\n\n"
        "def test_fibonacci_zero():\n"
        "    assert fibonacci(0) == 0\n\n"
        "def test_fibonacci_one():\n"
        "    assert fibonacci(1) == 1\n\n"
        "def test_fibonacci_ten():\n"
        "    assert fibonacci(10) == 55\n"
    ),
}


def _make_tool_calls(workspace: Path) -> list[dict]:
    """Return write_file tool_calls with absolute paths inside the isolated workspace.

    Absolute /tmp paths pass _check_file_access without a Flask request context.
    """
    return [
        {
            "name": "write_file",
            "args": {"path": str(workspace / rel), "content": content},
        }
        for rel, content in _FIBONACCI_CONTENT.items()
    ]


@pytest.fixture(autouse=True)
def _block_sgpt():
    with patch(
        "agent.cli_backends.sgpt.run_sgpt_command",
        side_effect=RuntimeError("sgpt_blocked_in_AFF-E2E"),
        create=True,
    ):
        yield


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "tests").mkdir(parents=True, exist_ok=True)
    return tmp_path


# Permissive guard_cfg: disables LLM tool guardrails so the 4 write_file calls
# (which exceed the default max_external_calls_per_request=2) are not rejected.
_PERMISSIVE_GUARD_CFG: dict = {"llm_tool_guardrails": {"enabled": False}}

_EXECUTION_POLICY = TaskExecutionPolicyContract(
    timeout_seconds=30,
    retries=0,
    retry_delay_seconds=0,
    source="e2e_test",
)


def _propose(workspace: Path, monkeypatch) -> "ProposeStrategyResult":
    monkeypatch.setattr("agent.config.settings.default_provider", "lmstudio")
    monkeypatch.setattr(
        "agent.services.model_invocation_service.ModelInvocationService.invoke_with_tools",
        make_mock_invoke_with_tools(_make_tool_calls(workspace)),
    )
    preset = get_task_kind_preset("new_software_project")
    policy = build_policy_from_dict(preset)
    context = ProposeContext(
        goal_id=GOAL_ID,
        task_id=TASK_ID,
        task={"task_kind": "new_software_project"},
        base_prompt="Create a Fibonacci REST API with Flask",
        tool_definitions_resolver=lambda: [
            {"name": "write_file", "description": "Write content to a file"},
        ],
        policy=policy,
    )
    registry = build_strategy_registry()
    orch = ProposeStrategyOrchestrator(policy, registry)
    return orch.run(context)


def _execute(workspace: Path, tool_calls: list[dict], app) -> "LocalExecutionResult":
    svc = TaskExecutionService()
    with app.app_context():
        return svc.execute_local_step(
            tid=TASK_ID,
            task={"task_kind": "new_software_project"},
            command=None,
            tool_calls=tool_calls,
            execution_policy=_EXECUTION_POLICY,
            guard_cfg=_PERMISSIVE_GUARD_CFG,
        )


# ─────────────────────────────────────────────────────────────────────────────
# AFF-E2E-T001: propose → execute → files on disk
# ─────────────────────────────────────────────────────────────────────────────

class TestProposeExecuteFilesOnDisk:
    """T001: prove that execute_local_step physically creates the LLM-proposed files."""

    def test_propose_selects_tool_calling_llm(self, workspace, monkeypatch, app):
        result = _propose(workspace, monkeypatch)

        assert result.status == STATUS_EXECUTABLE, (
            f"Expected executable, got {result.status}: {result.reason}"
        )
        assert result.metadata.get("selected_strategy") == STRATEGY_TOOL_CALLING_LLM
        assert result.metadata.get("selected_strategy") != "deterministic_handler", (
            "deterministic_handler was selected before LLM — LLM-first policy violated"
        )

    def test_propose_not_sgpt(self, workspace, monkeypatch, app):
        # _block_sgpt fixture raises if called; reaching here means sgpt was not used
        result = _propose(workspace, monkeypatch)
        assert result.is_executable

    def test_execute_writes_all_expected_files(self, workspace, monkeypatch, app):
        propose_result = _propose(workspace, monkeypatch)
        assert propose_result.is_executable

        _execute(workspace, propose_result.proposal.tool_calls, app)

        for rel in EXPECTED_RELATIVE_PATHS:
            assert (workspace / rel).exists(), f"Expected file not written by execute: {rel}"

    def test_execute_exit_code_zero(self, workspace, monkeypatch, app):
        propose_result = _propose(workspace, monkeypatch)
        exec_result = _execute(workspace, propose_result.proposal.tool_calls, app)

        assert exec_result.exit_code in (0, None), (
            f"Expected exit_code 0 or None, got {exec_result.exit_code}"
        )

    def test_files_have_expected_content(self, workspace, monkeypatch, app):
        propose_result = _propose(workspace, monkeypatch)
        _execute(workspace, propose_result.proposal.tool_calls, app)

        assert "fibonacci" in (workspace / "app.py").read_text().lower()
        assert "flask" in (workspace / "requirements.txt").read_text().lower()
        assert "fibonacci" in (workspace / "README.md").read_text().lower()
        assert "fibonacci" in (workspace / "tests" / "test_app.py").read_text().lower()


# ─────────────────────────────────────────────────────────────────────────────
# AFF-E2E-T002: artifact collection via synthesized workspace diff
# ─────────────────────────────────────────────────────────────────────────────

class TestArtifactCollectionAfterExecute:
    """T002: prove that WorkerOutputCollectorService collects generated files as artifacts."""

    def _run_full_flow(self, workspace: Path, monkeypatch, app) -> dict:
        """Propose + execute + collect. Returns collection_result."""
        diff_svc = WorkspaceDiffService()
        before_id, before_snap = diff_svc.take_before_snapshot(workspace)

        propose_result = _propose(workspace, monkeypatch)
        assert propose_result.is_executable
        _execute(workspace, propose_result.proposal.tool_calls, app)

        after_id, after_snap = diff_svc.take_after_snapshot(workspace)

        collector = get_worker_output_collector_service()
        return collector.collect(
            task_id=TASK_ID,
            goal_id=GOAL_ID,
            execution_id=EXECUTION_ID,
            trace_id=TRACE_ID,
            workspace_root=workspace,
            manifest_relative_path=".ananta/handoff/exec-fib-full-e2e-001/artifact_manifest.v1.json",
            allow_synthesized_fallback=True,
            before_snapshot_id=before_id,
            before_snapshot=before_snap,
            after_snapshot_id=after_id,
            after_snapshot=after_snap,
        )

    def test_collection_method_is_synthesized(self, workspace, monkeypatch, app):
        collection = self._run_full_flow(workspace, monkeypatch, app)

        assert collection["collection_method"] == "synthesized_from_diff", (
            f"Expected synthesized_from_diff, got {collection['collection_method']!r}"
        )
        assert collection.get("synthesized") is True

    def test_collection_is_valid(self, workspace, monkeypatch, app):
        collection = self._run_full_flow(workspace, monkeypatch, app)

        assert collection["manifest_valid"], (
            f"Collection must be valid; errors={collection.get('errors')}"
        )
        assert collection["artifacts"], "Artifact list must not be empty after execution"

    def test_all_expected_files_are_collected_as_artifacts(self, workspace, monkeypatch, app):
        collection = self._run_full_flow(workspace, monkeypatch, app)

        artifact_paths = {
            str(a.get("relative_path") or "") for a in collection["artifacts"]
        }
        for rel in EXPECTED_RELATIVE_PATHS:
            assert rel in artifact_paths, (
                f"Expected artifact not collected: {rel}. Found: {artifact_paths}"
            )

    def test_collected_artifacts_exist_on_disk(self, workspace, monkeypatch, app):
        collection = self._run_full_flow(workspace, monkeypatch, app)

        for artifact in collection["artifacts"]:
            assert artifact.get("_exists") is True, (
                f"Artifact {artifact.get('relative_path')!r} must exist on disk"
            )

    def test_unsafe_path_not_collected(self, workspace, monkeypatch, app):
        """Synthesized diff must not include path traversal artifacts."""
        collection = self._run_full_flow(workspace, monkeypatch, app)

        for artifact in collection["artifacts"]:
            rel = str(artifact.get("relative_path") or "")
            assert not rel.startswith("/"), f"Absolute path in artifact: {rel!r}"
            assert ".." not in rel.split("/"), f"Path traversal in artifact: {rel!r}"


# ─────────────────────────────────────────────────────────────────────────────
# AFF-E2E-T003: TaskCompletionPolicyService returns completed from artifact evidence
# ─────────────────────────────────────────────────────────────────────────────

class TestCompletionFromArtifacts:
    """T003: prove TaskCompletionPolicyService returns completed based on artifacts, not prose."""

    def _full_flow_collection(self, workspace: Path, monkeypatch, app) -> dict:
        diff_svc = WorkspaceDiffService()
        before_id, before_snap = diff_svc.take_before_snapshot(workspace)

        propose_result = _propose(workspace, monkeypatch)
        assert propose_result.is_executable
        _execute(workspace, propose_result.proposal.tool_calls, app)

        after_id, after_snap = diff_svc.take_after_snapshot(workspace)

        collector = get_worker_output_collector_service()
        return collector.collect(
            task_id=TASK_ID,
            goal_id=GOAL_ID,
            execution_id=EXECUTION_ID,
            trace_id=TRACE_ID,
            workspace_root=workspace,
            manifest_relative_path=".ananta/handoff/exec-fib-full-e2e-001/artifact_manifest.v1.json",
            allow_synthesized_fallback=True,
            before_snapshot_id=before_id,
            before_snapshot=before_snap,
            after_snapshot_id=after_id,
            after_snapshot=after_snap,
        )

    def test_happy_path_returns_completed(self, workspace, monkeypatch, app):
        collection = self._full_flow_collection(workspace, monkeypatch, app)

        svc = get_task_completion_policy_service()
        decision = svc.evaluate(
            task_id=TASK_ID,
            goal_id=GOAL_ID,
            collection_result=collection,
            advisory_parse_result=None,
            exit_code=0,
            retry_count=0,
            expected_paths=EXPECTED_RELATIVE_PATHS,
            verification_required=False,
            allow_synthesized_manifest=True,
        )

        assert decision.decision == "completed", (
            f"Expected completed from artifact evidence, got {decision.decision!r}. "
            f"reason_codes={decision.reason_codes}"
        )

    def test_completed_status_maps_correctly(self, workspace, monkeypatch, app):
        collection = self._full_flow_collection(workspace, monkeypatch, app)

        svc = get_task_completion_policy_service()
        decision = svc.evaluate(
            task_id=TASK_ID,
            goal_id=GOAL_ID,
            collection_result=collection,
            exit_code=0,
            expected_paths=EXPECTED_RELATIVE_PATHS,
            allow_synthesized_manifest=True,
        )
        status = svc.to_status(decision)

        assert status == "completed", f"to_status must map to 'completed', got {status!r}"

    def test_prose_only_does_not_complete(self, workspace, monkeypatch, app):
        """Advisory model prose must not drive completion when artifacts are absent."""
        diff_svc = WorkspaceDiffService()
        before_id, before_snap = diff_svc.take_before_snapshot(workspace)
        # No execute step — workspace is empty
        after_id, after_snap = diff_svc.take_after_snapshot(workspace)

        collector = get_worker_output_collector_service()
        empty_collection = collector.collect(
            task_id=TASK_ID,
            goal_id=GOAL_ID,
            execution_id=EXECUTION_ID,
            trace_id=TRACE_ID,
            workspace_root=workspace,
            manifest_relative_path=".ananta/handoff/exec-fib-full-e2e-001/artifact_manifest.v1.json",
            allow_synthesized_fallback=True,
            before_snapshot_id=before_id,
            before_snapshot=before_snap,
            after_snapshot_id=after_id,
            after_snapshot=after_snap,
        )

        # Model prose claiming success
        from agent.services.planning_utils import parse_followup_analysis
        advisory = parse_followup_analysis(
            '{"task_complete": true, "summary": "All Fibonacci files created successfully!"}'
        )

        svc = get_task_completion_policy_service()
        decision = svc.evaluate(
            task_id=TASK_ID,
            goal_id=GOAL_ID,
            collection_result=empty_collection,
            advisory_parse_result=advisory,
            exit_code=0,
            retry_count=0,
            expected_paths=EXPECTED_RELATIVE_PATHS,
            allow_synthesized_manifest=True,
        )

        assert decision.decision != "completed", (
            f"Prose-only success must not produce completed; got {decision.decision!r}. "
            "Completion must come from artifact evidence."
        )

    def test_completion_based_on_artifacts_not_advisory(self, workspace, monkeypatch, app):
        """When artifacts exist but advisory parse fails, completion still succeeds."""
        collection = self._full_flow_collection(workspace, monkeypatch, app)

        # Simulated advisory parse failure (malformed model JSON)
        failed_advisory = {"advisory": True, "parse_error": True, "task_complete": None}

        svc = get_task_completion_policy_service()
        decision = svc.evaluate(
            task_id=TASK_ID,
            goal_id=GOAL_ID,
            collection_result=collection,
            advisory_parse_result=failed_advisory,
            exit_code=0,
            retry_count=0,
            expected_paths=EXPECTED_RELATIVE_PATHS,
            allow_synthesized_manifest=True,
        )

        assert decision.decision == "completed", (
            "Advisory parse failure must not block artifact-first completion"
        )
        assert "advisory_parse_failed_ignored" in (decision.reason_codes or [])
