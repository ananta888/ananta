"""HDE-017: validation tests run in isolated temp workspaces."""
import json

import pytest

from agent.services.custom_tool_proposal_service import CustomToolProposalService
from agent.services.custom_tool_validation_service import CustomToolValidationService


def _proposal(tests=None, **overrides):
    payload = {
        "name": "custom.count_lines",
        "description": "Count lines of a file",
        "proposed_by": "user:test",
        "source_task_id": "task-1",
        "risk_class": "read",
        "category": "read_only",
        "execution_plane": "worker_runtime",
        "mutation_declaration": "read_only",
        "argument_schema": {"type": "object", "properties": {"path": {"type": "string"}}},
        "execution_kind": "command_template",
        "command_template": ["wc", "-l", "{path}"],
        "path_arguments": ["path"],
        "allowed_paths": ["**"],
        "denied_paths": [],
        "timeout_seconds": 10,
        "output_max_chars": 2000,
        "tests": tests
        or [
            {
                "name": "counts",
                "kind": "positive",
                "setup_files": {"a.txt": "eins\nzwei\n"},
                "arguments": {"path": "a.txt"},
                "expect_exit_code": 0,
                "expect_status": "ok",
                "expect_output_contains": ["2"],
                "expect_changed_paths": [],
            },
            {
                "name": "missing file fails",
                "kind": "negative",
                "arguments": {"path": "missing.txt"},
                "expect_status": "error",
            },
        ],
    }
    payload.update(overrides)
    return payload


def test_valid_proposal_passes_validation(tmp_path):
    service = CustomToolProposalService(tmp_path)
    stored = service.create_proposal(_proposal())
    passed, report_ref, report = CustomToolValidationService(tmp_path).validate_proposal(stored)
    assert passed is True
    assert report["passed"] is True
    assert len(report["cases"]) == 2
    report_path = tmp_path / report_ref
    assert report_path.is_file()
    assert json.loads(report_path.read_text())["proposal_digest"] == stored["proposal_digest"]


def test_failing_expectation_fails_validation(tmp_path):
    tests = [
        {
            "name": "wrong expectation",
            "kind": "positive",
            "setup_files": {"a.txt": "eins\n"},
            "arguments": {"path": "a.txt"},
            "expect_output_contains": ["drei"],
        },
        {"name": "negative", "kind": "negative", "arguments": {"path": "missing.txt"}, "expect_status": "error"},
    ]
    service = CustomToolProposalService(tmp_path)
    stored = service.create_proposal(_proposal(tests=tests))
    passed, _, report = CustomToolValidationService(tmp_path).validate_proposal(stored)
    assert passed is False
    failing = next(case for case in report["cases"] if case["name"] == "wrong expectation")
    assert any("missing_output_fragment" in failure for failure in failing["failures"])


def test_proposal_without_negative_case_cannot_be_validated(tmp_path):
    proposal = _proposal()
    proposal["tests"] = [proposal["tests"][0]]
    # bypass create_proposal (it would reject) to prove the validator
    # independently refuses incomplete test suites:
    proposal["proposal_digest"] = "manual-digest"
    passed, _, report = CustomToolValidationService(tmp_path).validate_proposal(proposal)
    assert passed is False
    assert report["error"] == "tests_missing_positive_or_negative_case"


def test_validation_runs_in_isolated_workspace_not_cwd(tmp_path, monkeypatch):
    """Setup files never land in the hub working directory."""
    monkeypatch.chdir(tmp_path)
    service = CustomToolProposalService(tmp_path / "data")
    stored = service.create_proposal(_proposal())
    CustomToolValidationService(tmp_path / "data").validate_proposal(stored)
    assert not (tmp_path / "a.txt").exists()


def test_forbidden_output_fragment_fails_case(tmp_path):
    tests = [
        {
            "name": "leaks forbidden output",
            "kind": "positive",
            "setup_files": {"a.txt": "geheim\n"},
            "arguments": {"path": "a.txt"},
            "expect_output_not_contains": ["1"],
        },
        {"name": "negative", "kind": "negative", "arguments": {"path": "missing.txt"}, "expect_status": "error"},
    ]
    service = CustomToolProposalService(tmp_path)
    stored = service.create_proposal(_proposal(tests=tests))
    passed, _, report = CustomToolValidationService(tmp_path).validate_proposal(stored)
    assert passed is False
