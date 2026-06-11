"""ALWA-012: tests for the workspace-audit event helper + canonical constants.

The helper emits workspace_* audit events with ALWA-DD-006 redaction
(prompt / raw_messages / full_diff / file_content are dropped),
changed_paths are sorted + truncated with a count and a flag, and
only paths / hashes / IDs / short summaries land in the audit row.
"""
from __future__ import annotations

import pytest

from agent.common.audit import (
    AUDIT_WORKSPACE_BASELINE_CREATED,
    AUDIT_WORKSPACE_MUTATION_BLOCKED,
    AUDIT_WORKSPACE_MUTATION_EVALUATED,
    audit_workspace_mutation_event,
)


def test_workspace_audit_constants_exist() -> None:
    assert AUDIT_WORKSPACE_BASELINE_CREATED == "workspace_baseline_created"
    assert AUDIT_WORKSPACE_MUTATION_EVALUATED == "workspace_mutation_evaluated"
    assert AUDIT_WORKSPACE_MUTATION_BLOCKED == "workspace_mutation_blocked"


def test_helper_redacts_prompt_and_full_diff(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    def fake_log(action: str, details: dict) -> None:
        captured["action"] = action
        captured["details"] = details

    monkeypatch.setattr("agent.common.audit.log_audit", fake_log)

    audit_workspace_mutation_event(
        AUDIT_WORKSPACE_MUTATION_BLOCKED,
        task_id="t1",
        goal_id="g1",
        trace_id="tr1",
        iteration_number=3,
        mutation_mode="controlled_workspace",
        changed_paths=["b.py", "a.py"],
        diff_hash="abc123",
        policy_decision="violation",
        violation_ids=["V001"],
        violation_summary="outside manifest",
        blocked_reason="forbidden_path",
        prompt="leak me",
        raw_messages=["x"],
        full_diff="--- huge diff ---",
    )

    assert captured["action"] == "workspace_mutation_blocked"
    details = captured["details"]
    assert "prompt" not in details
    assert "raw_messages" not in details
    assert "full_diff" not in details
    # changed_paths are sorted deterministically.
    assert details["changed_paths"] == ["a.py", "b.py"]
    assert details["diff_hash"] == "abc123"
    assert details["blocked_reason"] == "forbidden_path"


def test_helper_truncates_changed_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    def fake_log(action: str, details: dict) -> None:
        captured["action"] = action
        captured["details"] = details

    monkeypatch.setattr("agent.common.audit.log_audit", fake_log)

    audit_workspace_mutation_event(
        AUDIT_WORKSPACE_MUTATION_EVALUATED,
        task_id="t",
        changed_paths=[f"path_{i}.py" for i in range(200)],
        diff_hash="h",
        policy_decision="allowed",
    )

    assert captured["action"] == "workspace_mutation_evaluated"
    details = captured["details"]
    assert len(details["changed_paths"]) <= 50
    assert details["changed_paths_truncated"] is True
    assert details["changed_paths_count"] == 200
    # Paths are sorted, so the first kept entries are the lex-smallest.
    assert details["changed_paths"] == sorted(details["changed_paths"])
