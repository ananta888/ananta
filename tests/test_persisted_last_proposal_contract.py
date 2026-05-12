"""Tests for propose→persist→execute proposal roundtrip — AFR-FINAL-T007.

Proves that a proposal produced by any real strategy can be persisted as a dict
and later reloaded by validate_executable_proposal without shape mismatch.
"""
from __future__ import annotations

import pytest

from worker.core.propose import (
    ExecutableProposal,
    ProposeStrategyResult,
    validate_executable_proposal,
    AdvisoryProposalArtifact,
)


def _make_proposal_dict(
    *,
    command: str | None = None,
    tool_calls: list | None = None,
    reason: str | None = None,
    backend: str = "orchestrator",
    task_kind: str = "new_software_project",
    strategy: str = "tool_calling_llm",
) -> dict:
    """Simulate what persist_task_proposal_result stores."""
    proposal: dict = {
        "reason": reason,
        "backend": backend,
        "model": None,
        "routing": {
            "task_kind": task_kind,
            "propose_strategy_meta": {
                "selected_strategy": strategy,
                "proposal_status": "executable",
            },
        },
        "trace": {"policy_version": "v1"},
        "worker_context": {"strategy": strategy},
        "review": None,
    }
    if command:
        proposal["command"] = command
    if tool_calls:
        proposal["tool_calls"] = tool_calls
    return proposal


class TestCommandProposalRoundtrip:
    def test_tool_calling_llm_command_proposal(self):
        raw = _make_proposal_dict(
            command="pip install fastapi && mkdir src",
            reason="json_schema_matched",
            strategy="json_schema_llm",
        )
        cmd, tcs, reason = validate_executable_proposal(raw)
        assert cmd == "pip install fastapi && mkdir src"
        assert tcs == []
        assert reason == "json_schema_matched"

    def test_json_schema_llm_command_proposal(self):
        raw = _make_proposal_dict(
            command="make build",
            strategy="json_schema_llm",
        )
        cmd, tcs, _ = validate_executable_proposal(raw)
        assert cmd == "make build"


class TestToolCallsProposalRoundtrip:
    def test_tool_calls_from_tool_calling_llm(self):
        tool_calls = [
            {"name": "write_file", "args": {"path": "app.py", "content": "from flask import Flask"}},
            {"name": "write_file", "args": {"path": "requirements.txt", "content": "flask>=2.0\n"}},
        ]
        raw = _make_proposal_dict(tool_calls=tool_calls, strategy="tool_calling_llm")
        cmd, tcs, _ = validate_executable_proposal(raw)
        assert cmd is None
        assert len(tcs) == 2
        assert tcs[0]["name"] == "write_file"

    def test_tool_calls_from_flexible_normalization(self):
        tool_calls = [{"name": "write_file", "args": {"path": "main.py", "content": "..."}}, ]
        raw = _make_proposal_dict(
            tool_calls=tool_calls,
            strategy="flexible_llm_normalization",
        )
        _, tcs, _ = validate_executable_proposal(raw)
        assert len(tcs) == 1


class TestInvalidPersistedShapeRejected:
    def test_missing_both_command_and_tool_calls(self):
        raw = _make_proposal_dict()  # neither command nor tool_calls
        with pytest.raises(ValueError, match="executable_proposal_requires_command_or_tool_calls"):
            validate_executable_proposal(raw)

    def test_non_executable_proposal_status_is_not_persisted(self):
        """Advisory results are never persisted as last_proposal for execute.

        This test documents that the orchestrator only persists executable results.
        """
        advisory = ProposeStrategyResult.advisory(
            "tool_calling_llm",
            advisory_text="Consider using FastAPI",
        )
        assert not advisory.is_executable
        # Attempting to call to_dict on an advisory result should not produce command/tool_calls
        d = advisory.to_dict()
        assert d.get("proposal") is None or not d.get("proposal", {}).get("command")

    def test_needs_review_result_not_persisted_as_executable(self):
        result = ProposeStrategyResult.needs_review("orchestrator", "llm_required_but_unavailable")
        assert not result.is_executable

    def test_failed_result_not_persisted_as_executable(self):
        result = ProposeStrategyResult.failed("tool_calling_llm", "connection_error")
        assert not result.is_executable


class TestExecutableProposalToDict:
    """Validate that ExecutableProposal.to_dict() produces the canonical schema for persistence."""

    def test_schema_field_present(self):
        p = ExecutableProposal.from_command(
            goal_id="g1", task_id="t1", strategy_id="s1", command="echo hi"
        )
        d = p.to_dict()
        assert d["schema"] == "executable_proposal.v1"

    def test_all_required_fields_in_dict(self):
        p = ExecutableProposal.from_tool_calls(
            goal_id="g1", task_id="t1", strategy_id="s1",
            tool_calls=[{"name": "run", "args": {}}],
        )
        d = p.to_dict()
        for field in ("proposal_id", "goal_id", "task_id", "strategy_id", "command",
                      "tool_calls", "required_tools", "expected_artifacts", "safety_flags",
                      "reason", "created_at", "metadata"):
            assert field in d, f"missing field: {field}"

    def test_roundtrip_command_via_validate(self):
        p = ExecutableProposal.from_command(
            goal_id="g1", task_id="t1", strategy_id="s1", command="make test"
        )
        d = p.to_dict()
        cmd, tcs, _ = validate_executable_proposal(d)
        assert cmd == "make test"

    def test_roundtrip_tool_calls_via_validate(self):
        tc = [{"name": "write_file", "args": {"path": "f.py"}}]
        p = ExecutableProposal.from_tool_calls(
            goal_id="g1", task_id="t1", strategy_id="s1", tool_calls=tc
        )
        d = p.to_dict()
        _, tcs, _ = validate_executable_proposal(d)
        assert tcs == tc
