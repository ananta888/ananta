"""Tests for ExecutableProposal validation in the execute path — AFR-FINAL-T001.

Proves that execute_task_step uses validate_executable_proposal (dataclass-safe),
not the Pydantic model_validate, and that invalid proposal shapes produce
deterministic denied responses.
"""
from __future__ import annotations

import pytest

from worker.core.propose import (
    ExecutableProposal,
    validate_executable_proposal,
    AdvisoryProposalArtifact,
    PatchProposalArtifact,
    FileProposalArtifact,
)


class TestValidateExecutableProposal:
    """Unit tests for the validate_executable_proposal helper."""

    def test_valid_dict_with_command(self):
        raw = {"command": "echo hello", "tool_calls": []}
        cmd, tcs, reason = validate_executable_proposal(raw)
        assert cmd == "echo hello"
        assert tcs == []
        assert reason is None

    def test_valid_dict_with_tool_calls(self):
        raw = {"tool_calls": [{"name": "write_file", "args": {}}], "command": None}
        cmd, tcs, reason = validate_executable_proposal(raw)
        assert cmd is None
        assert tcs == [{"name": "write_file", "args": {}}]

    def test_valid_dict_reason_preserved(self):
        raw = {"command": "make build", "reason": "template_matched"}
        _, _, reason = validate_executable_proposal(raw)
        assert reason == "template_matched"

    def test_valid_executable_proposal_instance(self):
        p = ExecutableProposal.from_command(
            goal_id="g1", task_id="t1", strategy_id="s1", command="ls -la"
        )
        cmd, tcs, reason = validate_executable_proposal(p)
        assert cmd == "ls -la"
        assert tcs == []

    def test_valid_tool_calls_instance(self):
        tc = [{"name": "write_file", "args": {"path": "main.py"}}]
        p = ExecutableProposal.from_tool_calls(
            goal_id="g1", task_id="t1", strategy_id="s1", tool_calls=tc
        )
        cmd, tcs, _ = validate_executable_proposal(p)
        assert cmd is None
        assert tcs == tc

    def test_missing_both_raises_valueerror(self):
        raw = {"reason": "some_reason"}
        with pytest.raises(ValueError, match="executable_proposal_requires_command_or_tool_calls"):
            validate_executable_proposal(raw)

    def test_empty_command_string_raises(self):
        raw = {"command": "   ", "tool_calls": []}
        with pytest.raises(ValueError, match="executable_proposal_requires_command_or_tool_calls"):
            validate_executable_proposal(raw)

    def test_none_command_and_empty_tool_calls_raises(self):
        raw = {"command": None, "tool_calls": []}
        with pytest.raises(ValueError, match="executable_proposal_requires_command_or_tool_calls"):
            validate_executable_proposal(raw)

    def test_invalid_type_raises_valueerror(self):
        with pytest.raises(ValueError, match="invalid_proposal_type"):
            validate_executable_proposal("not a dict or proposal")

    def test_tool_calls_non_list_treated_as_empty(self):
        raw = {"command": None, "tool_calls": "not_a_list"}
        # tool_calls non-list → treated as empty, command is None → ValueError
        with pytest.raises(ValueError):
            validate_executable_proposal(raw)

    def test_command_whitespace_stripped_to_none(self):
        raw = {"command": "\n  \t", "tool_calls": []}
        with pytest.raises(ValueError):
            validate_executable_proposal(raw)

    def test_full_persisted_proposal_shape(self):
        """Simulates the dict stored by persist_task_proposal_result."""
        raw = {
            "reason": "template_matched",
            "backend": "orchestrator",
            "model": None,
            "routing": {"task_kind": "new_software_project"},
            "command": "mkdir my-project && cd my-project && git init",
            "tool_calls": [],
            "trace": {"policy_version": "v1"},
            "worker_context": {"strategy": "deterministic_handler"},
        }
        cmd, tcs, reason = validate_executable_proposal(raw)
        assert cmd == "mkdir my-project && cd my-project && git init"
        assert tcs == []
        assert reason == "template_matched"

    def test_tool_calls_proposal_from_llm_strategy(self):
        """Simulates dict stored after tool_calling_llm_strategy succeeds."""
        raw = {
            "reason": None,
            "backend": "orchestrator",
            "command": None,
            "tool_calls": [
                {"name": "write_file", "args": {"path": "app.py", "content": "# fib\n"}},
                {"name": "write_file", "args": {"path": "requirements.txt", "content": "flask\n"}},
            ],
        }
        cmd, tcs, _ = validate_executable_proposal(raw)
        assert cmd is None
        assert len(tcs) == 2
        assert tcs[0]["name"] == "write_file"


class TestAdvisoryProposalRejectedByValidation:
    """Advisory/patch/file proposals must not be executable."""

    def test_advisory_artifact_dict_rejected(self):
        raw = {
            "schema": "advisory_proposal_artifact.v1",
            "advisory_text": "Consider adding tests.",
        }
        with pytest.raises(ValueError, match="executable_proposal_requires_command_or_tool_calls"):
            validate_executable_proposal(raw)

    def test_patch_proposal_dict_rejected(self):
        raw = {
            "schema": "patch_proposal_artifact.v1",
            "patches": [{"path": "file.py", "content": "..."}],
        }
        with pytest.raises(ValueError):
            validate_executable_proposal(raw)

    def test_file_proposal_dict_rejected(self):
        raw = {
            "schema": "file_proposal_artifact.v1",
            "files": [{"path": "main.py", "content": "print('hi')"}],
        }
        with pytest.raises(ValueError):
            validate_executable_proposal(raw)

    def test_planner_proposal_dict_rejected(self):
        raw = {
            "schema": "planner_proposal_artifact.v1",
            "sub_tasks": [{"task_id": "t1", "title": "T", "description": "D", "kind": "coding"}],
        }
        with pytest.raises(ValueError):
            validate_executable_proposal(raw)


class TestNoModelValidateInCodebase:
    """Regression: model_validate must not be called on ExecutableProposal."""

    def test_executable_proposal_has_no_model_validate(self):
        assert not hasattr(ExecutableProposal, "model_validate"), (
            "ExecutableProposal is a dataclass — model_validate belongs to Pydantic. "
            "Any caller of model_validate(proposal) will crash at runtime."
        )
