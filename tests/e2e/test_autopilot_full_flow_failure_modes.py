"""AFF-E2E-T006: full-flow failure-mode and security regression tests.

These tests prove the artifact-first model is robust when execution or verification
fails, and that unsafe paths are not accepted as valid completion evidence.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from agent.models import TaskExecutionPolicyContract
from agent.services.task_completion_policy_service import get_task_completion_policy_service
from agent.services.task_execution_service import TaskExecutionService
from agent.services.worker_output_collector_service import get_worker_output_collector_service
from agent.services.workspace_diff_service import WorkspaceDiffService


TASK_ID = "task-failure-modes-001"
GOAL_ID = "goal-failure-modes-001"
EXECUTION_ID = "exec-failure-modes-001"
TRACE_ID = "trace-failure-modes-001"

_EXEC_POLICY = TaskExecutionPolicyContract(
    timeout_seconds=30,
    retries=0,
    retry_delay_seconds=0,
    source="e2e_failure_modes",
)


@pytest.fixture(autouse=True)
def _block_sgpt():
    with patch(
        "agent.cli_backends.sgpt.run_sgpt_command",
        side_effect=RuntimeError("sgpt_blocked_in_AFF-E2E-T006"),
        create=True,
    ):
        yield


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "tests").mkdir(parents=True, exist_ok=True)
    return tmp_path


def _collect(workspace: Path, before_id, before_snap, after_id, after_snap) -> dict:
    collector = get_worker_output_collector_service()
    return collector.collect(
        task_id=TASK_ID,
        goal_id=GOAL_ID,
        execution_id=EXECUTION_ID,
        trace_id=TRACE_ID,
        workspace_root=workspace,
        manifest_relative_path=".ananta/handoff/exec-failure-modes/artifact_manifest.v1.json",
        allow_synthesized_fallback=True,
        before_snapshot_id=before_id,
        before_snapshot=before_snap,
        after_snapshot_id=after_id,
        after_snapshot=after_snap,
    )


def test_outside_workspace_write_does_not_complete(workspace, app):
    svc = TaskExecutionService()
    diff = WorkspaceDiffService()
    before_id, before_snap = diff.take_before_snapshot(workspace)

    outside = workspace.parent / "outside_should_not_count.py"
    tool_calls = [{"name": "write_file", "args": {"path": str(outside), "content": "x=1\n"}}]

    with app.app_context():
        _ = svc.execute_local_step(
            tid=TASK_ID,
            task={"task_kind": "new_software_project"},
            command=None,
            tool_calls=tool_calls,
            execution_policy=_EXEC_POLICY,
            guard_cfg={"llm_tool_guardrails": {"enabled": False}},
        )

    after_id, after_snap = diff.take_after_snapshot(workspace)
    collection = _collect(workspace, before_id, before_snap, after_id, after_snap)

    completion = get_task_completion_policy_service().evaluate(
        task_id=TASK_ID,
        goal_id=GOAL_ID,
        collection_result=collection,
        exit_code=0,
        expected_paths=["app.py"],
        verification_required=False,
        allow_synthesized_manifest=True,
    )

    assert completion.decision != "completed"


def test_missing_required_artifacts_do_not_complete(workspace, app):
    svc = TaskExecutionService()
    diff = WorkspaceDiffService()
    before_id, before_snap = diff.take_before_snapshot(workspace)

    tool_calls = [{"name": "write_file", "args": {"path": str(workspace / "README.md"), "content": "ok\n"}}]
    with app.app_context():
        _ = svc.execute_local_step(
            tid=TASK_ID,
            task={"task_kind": "new_software_project"},
            command=None,
            tool_calls=tool_calls,
            execution_policy=_EXEC_POLICY,
            guard_cfg={"llm_tool_guardrails": {"enabled": False}},
        )

    after_id, after_snap = diff.take_after_snapshot(workspace)
    collection = _collect(workspace, before_id, before_snap, after_id, after_snap)

    completion = get_task_completion_policy_service().evaluate(
        task_id=TASK_ID,
        goal_id=GOAL_ID,
        collection_result=collection,
        exit_code=0,
        expected_paths=["app.py", "tests/test_app.py"],
        verification_required=False,
        allow_synthesized_manifest=True,
    )

    assert completion.decision != "completed"


def test_failed_verification_does_not_complete(workspace):
    collection = {
        "manifest_valid": True,
        "synthesized": True,
        "collection_method": "synthesized_from_diff",
        "errors": [],
        "warnings": [],
        "artifacts": [
            {"artifact_id": "a1", "relative_path": "app.py", "_exists": True, "required": True, "verification_status": "unverified"},
            {"artifact_id": "a2", "relative_path": "tests/test_app.py", "_exists": True, "required": True, "verification_status": "unverified"},
        ],
    }

    completion = get_task_completion_policy_service().evaluate(
        task_id=TASK_ID,
        goal_id=GOAL_ID,
        collection_result=collection,
        exit_code=0,
        expected_paths=["app.py", "tests/test_app.py"],
        verification_required=True,
        allow_synthesized_manifest=True,
    )

    assert completion.decision != "completed"


def test_prose_claim_success_without_artifacts_does_not_complete(workspace):
    collection = {
        "manifest_valid": True,
        "synthesized": True,
        "collection_method": "synthesized_from_diff",
        "errors": [],
        "warnings": [],
        "artifacts": [],
    }

    advisory = {"advisory": True, "task_complete": True, "summary": "Done"}
    completion = get_task_completion_policy_service().evaluate(
        task_id=TASK_ID,
        goal_id=GOAL_ID,
        collection_result=collection,
        advisory_parse_result=advisory,
        exit_code=0,
        expected_paths=["app.py"],
        verification_required=False,
        allow_synthesized_manifest=True,
    )

    assert completion.decision != "completed"
