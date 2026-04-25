from __future__ import annotations

import pytest

from worker.core.trace import attach_trace_to_result, build_trace_metadata, ensure_trace_metadata, stable_hash


def test_trace_metadata_attach_includes_required_fields() -> None:
    metadata = build_trace_metadata(
        trace_id="tr-1",
        task_id="task-1",
        capability_id="worker.command.execute",
        context_hash="ctx-1",
        policy_decision_ref={"decision_id": "d-1", "decision": "allow", "policy_version": "v1"},
        approval_ref={"approval_id": "a-1", "status": "approved"},
    )
    result = attach_trace_to_result(
        result={
            "schema": "worker_execution_result.v1",
            "task_id": "task-1",
            "trace_id": "tr-1",
            "status": "completed",
            "artifacts": [{"artifact_type": "test_result_artifact", "artifact_ref": "test:1"}],
        },
        trace_metadata=metadata,
        mode="command_execute",
    )
    assert result["capability_id"] == "worker.command.execute"
    assert result["context_hash"] == "ctx-1"
    assert result["policy_decision_ref"]["decision"] == "allow"


def test_missing_trace_metadata_fails_execution_capable_mode() -> None:
    with pytest.raises(ValueError, match="missing_trace_metadata:context_hash"):
        ensure_trace_metadata(
            mode="patch_apply",
            metadata={
                "trace_id": "tr-1",
                "task_id": "task-1",
                "capability_id": "worker.patch.apply",
                "context_hash": "",
                "policy_decision_ref": {"decision_id": "d-1", "decision": "allow", "policy_version": "v1"},
            },
        )


def test_stable_hash_is_deterministic() -> None:
    assert stable_hash("pytest -q") == stable_hash("pytest -q")
