"""Tests for ExecutableProposal and ProposeStrategyResult contracts. FA-T001."""
from __future__ import annotations

import pytest

from worker.core.propose import (
    ExecutableProposal,
    ProposeStrategyResult,
    STATUS_EXECUTABLE,
    STATUS_DECLINED,
    STATUS_ADVISORY,
    STATUS_NEEDS_REVIEW,
    STATUS_FAILED,
    STATUS_POLICY_DENIED,
)


# ── ExecutableProposal ────────────────────────────────────────────────────────

class TestExecutableProposal:
    def test_from_command_valid(self):
        p = ExecutableProposal.from_command(
            goal_id="g1", task_id="t1", strategy_id="s1", command="echo hello"
        )
        assert p.command == "echo hello"
        assert p.tool_calls == []
        assert p.proposal_id.startswith("prop-")
        d = p.to_dict()
        assert d["schema"] == "executable_proposal.v1"
        assert d["command"] == "echo hello"

    def test_from_tool_calls_valid(self):
        tc = [{"type": "function", "function": {"name": "write_file", "arguments": "{}"}}]
        p = ExecutableProposal.from_tool_calls(
            goal_id="g1", task_id="t1", strategy_id="s1", tool_calls=tc
        )
        assert p.command is None
        assert len(p.tool_calls) == 1
        d = p.to_dict()
        assert d["tool_calls"] == tc

    def test_requires_command_or_tool_calls(self):
        with pytest.raises(ValueError, match="requires_command_or_tool_calls"):
            ExecutableProposal(
                proposal_id="p1", goal_id="g1", task_id="t1", strategy_id="s1"
            )

    def test_empty_tool_calls_rejected(self):
        with pytest.raises(ValueError, match="tool_calls_must_be_non_empty"):
            ExecutableProposal.from_tool_calls(
                goal_id="g1", task_id="t1", strategy_id="s1", tool_calls=[]
            )

    def test_command_wrong_type_rejected(self):
        with pytest.raises(TypeError):
            ExecutableProposal(
                proposal_id="p1", goal_id="g1", task_id="t1", strategy_id="s1",
                command=123,
            )

    def test_to_dict_contains_required_fields(self):
        p = ExecutableProposal.from_command(
            goal_id="g1", task_id="t1", strategy_id="s1", command="ls"
        )
        d = p.to_dict()
        for key in ("proposal_id", "goal_id", "task_id", "strategy_id", "command", "tool_calls",
                    "required_tools", "expected_artifacts", "safety_flags", "created_at"):
            assert key in d, f"missing key: {key}"

    def test_expected_artifacts_stored(self):
        p = ExecutableProposal.from_command(
            goal_id="g1", task_id="t1", strategy_id="s1", command="make",
            expected_artifacts=[{"kind": "generated_file", "path": "app.py"}],
        )
        assert len(p.expected_artifacts) == 1


# ── ProposeStrategyResult ─────────────────────────────────────────────────────

class TestProposeStrategyResult:
    def _proposal(self) -> ExecutableProposal:
        return ExecutableProposal.from_command(
            goal_id="g1", task_id="t1", strategy_id="s1", command="make"
        )

    def test_executable_result(self):
        p = self._proposal()
        r = ProposeStrategyResult.executable("s1", p, reason="handler_matched")
        assert r.is_executable
        assert r.proposal is p
        assert r.to_dict()["status"] == STATUS_EXECUTABLE

    def test_executable_requires_proposal(self):
        with pytest.raises(ValueError, match="executable_result_requires_ExecutableProposal"):
            ProposeStrategyResult(status=STATUS_EXECUTABLE, strategy_id="s1", proposal=None)

    def test_non_executable_must_not_carry_proposal(self):
        p = self._proposal()
        with pytest.raises(ValueError, match="non_executable_result_must_not_carry_ExecutableProposal"):
            ProposeStrategyResult(status=STATUS_DECLINED, strategy_id="s1", proposal=p)

    def test_declined_result(self):
        r = ProposeStrategyResult.declined("s1", reason="no_handler")
        assert r.status == STATUS_DECLINED
        assert not r.is_executable
        assert not r.is_terminal

    def test_advisory_result(self):
        r = ProposeStrategyResult.advisory("s1", advisory_text="consider using X")
        assert r.status == STATUS_ADVISORY
        assert r.advisory_text == "consider using X"

    def test_failed_is_terminal(self):
        r = ProposeStrategyResult.failed("s1", reason="parse_error")
        assert r.is_terminal

    def test_policy_denied_is_terminal(self):
        r = ProposeStrategyResult.policy_denied("s1")
        assert r.is_terminal

    def test_needs_review_not_terminal(self):
        r = ProposeStrategyResult.needs_review("s1")
        assert not r.is_terminal

    def test_invalid_status_rejected(self):
        with pytest.raises(ValueError, match="invalid_propose_strategy_result_status"):
            ProposeStrategyResult(status="unknown_status", strategy_id="s1")

    def test_free_text_only_is_not_executable(self):
        r = ProposeStrategyResult.advisory("s1", advisory_text="just prose output")
        assert r.status == STATUS_ADVISORY
        assert r.proposal is None

    def test_to_dict_complete(self):
        p = self._proposal()
        r = ProposeStrategyResult.executable("s1", p)
        d = r.to_dict()
        assert d["schema"] == "propose_strategy_result.v1"
        assert d["proposal"]["schema"] == "executable_proposal.v1"
