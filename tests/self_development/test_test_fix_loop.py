"""Test-fix loop: policy pre-authorization, runner contract, round transitions, carry."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.services._worker_workspace_context_writer import _declared_test_command_span
from agent.services.self_development.policy import DevelopmentPolicy
from agent.services.self_development.test_fix_loop import (
    build_runner_description,
    failure_feedback,
    fix_description,
    looks_like_test_report,
    minimum_command,
    next_round_action,
    parse_test_report,
    read_test_report,
    write_test_report,
)
from agent.services.self_development.workspace import carry_workspace

BASE_POLICY = {
    "enabled": True,
    "automatic_publication": True,
    "remote_url": "https://github.com/ananta888/ananta.git",
    "branch": "ananta/self-development",
    "control_root": "",
    "workspace_root": "",
    "allowed_roots": ["tests"],
    "test_profiles": {"all-tests": {"command": "python -m pytest -q tests"}},
}


def _policy(tmp_path: Path, **test_fix_loop) -> DevelopmentPolicy:
    value = {**BASE_POLICY, "control_root": str(tmp_path / "control"), "workspace_root": str(tmp_path / "work")}
    if test_fix_loop:
        value["test_fix_loop"] = test_fix_loop
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(value))
    return DevelopmentPolicy(path)


# --- policy schema ---------------------------------------------------------


def test_absent_block_means_disabled_with_bounded_defaults(tmp_path):
    cfg = _policy(tmp_path).test_fix_loop()
    assert cfg["enabled"] is False
    assert cfg["max_rounds"] == 3
    assert cfg["runner"] == "opencode"
    assert cfg["minimum_profile"] == "all-tests"
    assert cfg["round_timeout_seconds"] == 1800
    assert cfg["parallel_test_runner"] is False


def test_valid_block_is_accepted_and_normalized(tmp_path):
    cfg = _policy(
        tmp_path,
        enabled=True,
        max_rounds=5,
        minimum_profile="all-tests",
        minimum_args=["-m", "not slow"],
        round_timeout_seconds=900,
    ).test_fix_loop()
    assert cfg["enabled"] is True
    assert cfg["max_rounds"] == 5
    assert cfg["minimum_args"] == ["-m", "not slow"]
    assert cfg["round_timeout_seconds"] == 900


@pytest.mark.parametrize(
    "block, match",
    [
        ({"enabled": True, "surprise": 1}, "development_test_fix_loop_invalid"),
        ({"enabled": "yes"}, "development_test_fix_loop_invalid"),
        ({"max_rounds": 0}, "development_test_fix_loop_invalid"),
        ({"max_rounds": 11}, "development_test_fix_loop_invalid"),
        ({"runner": "sgpt"}, "development_test_fix_loop_invalid"),
        ({"minimum_profile": "does-not-exist"}, "development_test_fix_loop_invalid"),
        ({"minimum_args": ["tests; rm -rf /"]}, "development_test_fix_loop_invalid"),
        ({"minimum_args": ["a" * 81]}, "development_test_fix_loop_invalid"),
        ({"round_timeout_seconds": 30}, "development_test_fix_loop_invalid"),
        ({"parallel_test_runner": True}, "development_test_fix_loop_parallel_unsupported"),
    ],
)
def test_invalid_blocks_fail_closed(tmp_path, block, match):
    with pytest.raises(ValueError, match=match):
        _policy(tmp_path, **block)


# --- minimum program & runner brief ----------------------------------------


def test_minimum_command_joins_profile_and_approved_args(tmp_path):
    cfg = _policy(tmp_path, enabled=True, minimum_args=["-n", "4"]).test_fix_loop()
    assert minimum_command(cfg, {"all-tests": {"command": "python -m pytest -q tests"}}) == (
        "python -m pytest -q tests -n 4"
    )


def test_minimum_command_fails_closed_without_profile(tmp_path):
    cfg = _policy(tmp_path, enabled=True).test_fix_loop()
    with pytest.raises(ValueError, match="minimum_profile_unavailable"):
        minimum_command(cfg, {})


def test_runner_description_carries_the_declared_command_marker():
    cmd = minimum_command({"minimum_profile": "all-tests"}, {"all-tests": {"command": "python -m pytest -q tests"}})
    text = build_runner_description({"max_rounds": 3}, todo_id="T-1", title="Do the thing", minimum_cmd=cmd)
    span = _declared_test_command_span(text, "Run exactly:")
    assert span is not None and " ".join(text[span[0] : span[1]].split()) == cmd
    assert "aborted_early" in text and "ERWEITERN" in text
    assert "keine Aenderungen" in text


# --- report contract -------------------------------------------------------


def test_parse_accepts_a_clean_pass():
    report = parse_test_report(json.dumps({"passed": True, "ran": ["python -m pytest -q tests"], "failures": []}))
    assert report["outcome"] == "passed"


def test_parse_accepts_prose_wrapped_failures_and_bounds_them():
    text = "Here is my result:\n" + json.dumps(
        {
            "passed": False,
            "aborted_early": True,
            "ran": ["python -m pytest -q tests"],
            "expanded": ["python -m pytest -q tests/agent"],
            "failures": [{"file": "tests/a.py", "test": "test_x", "error_tail": "E" * 2000}],
        }
    )
    report = parse_test_report(text)
    assert report["outcome"] == "aborted_early"
    assert len(report["failures"][0]["error_tail"]) == 600


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        {"passed": "yes", "ran": ["x"], "failures": []},
        {"passed": True, "ran": ["x"], "failures": [{"file": "f", "test": "t"}]},
        {"passed": False, "ran": ["x"], "failures": []},
        {"passed": False, "aborted_early": True, "ran": ["x"], "failures": []},
        {"passed": False, "ran": [], "failures": [{"file": "f", "test": "t"}]},
        {"passed": False, "ran": ["x"], "failures": ["not-a-dict"]},
        {"passed": False, "ran": ["x"], "failures": [{"test": "missing-file-key"}]},
        {"passed": False, "ran": ["x"], "failures": [{"file": "f", "test": "t"}] * 51},
        {"passed": False, "ran": ["x" * 201], "failures": [{"file": "f", "test": "t"}]},
    ],
)
def test_parse_rejects_contradictory_or_out_of_contract_reports(payload):
    text = "nonsense" if payload is None else json.dumps(payload)
    assert parse_test_report(text) is None
    assert looks_like_test_report(text) is False


def test_report_roundtrip_is_task_fenced(tmp_path):
    workspace = tmp_path / "ws"
    answer = json.dumps({"passed": True, "ran": ["python -m pytest -q tests"], "failures": []})
    assert write_test_report(workspace, task_id="t-1", answer=answer) is True
    assert read_test_report(workspace, task_id="t-1")["passed"] is True
    assert read_test_report(workspace, task_id="t-2") is None
    assert read_test_report(tmp_path / "other", task_id="t-1") is None


def test_write_refuses_non_reports(tmp_path):
    assert write_test_report(tmp_path / "ws", task_id="t-1", answer="I ran everything, it was fine") is False
    assert not (tmp_path / "ws" / ".ananta" / "test-report.json").exists()


# --- round transitions & feedback ------------------------------------------


def test_transition_table_matches_round_semantics():
    cfg = {"max_rounds": 3}
    green = {"passed": True}
    red = {"passed": False}
    assert next_round_action({"test_fix_rounds": 1}, green, cfg) == "pass"
    assert next_round_action({"test_fix_rounds": 1}, red, cfg) == "fix"
    assert next_round_action({"test_fix_rounds": 2}, red, cfg) == "fix"
    assert next_round_action({"test_fix_rounds": 3}, red, cfg) == "exhausted"
    assert next_round_action({"test_fix_rounds": 1}, red, {"max_rounds": 1}) == "exhausted"


def test_fix_description_carries_bounded_failure_feedback():
    item = {"todo_id": "T-9", "description": "Original task text"}
    report = {
        "passed": False,
        "aborted_early": True,
        "ran": ["python -m pytest -q tests"],
        "expanded": [],
        "failures": [{"file": "tests/a.py", "test": "test_x", "error_tail": "boom"}],
    }
    description = fix_description({**item, "test_fix_rounds": 1}, report)
    assert description.startswith("Original task text")
    assert "Previous test-lane failures" in description
    assert '"test": "test_x"' in description
    assert len(description) <= 4000
    feedback = failure_feedback({**item, "test_fix_rounds": 1}, report)
    assert feedback["round"] == 1 and feedback["aborted_early"] is True


# --- workspace carry -------------------------------------------------------


def _source_workspace(tmp_path: Path) -> Path:
    source = tmp_path / "self-dev-old"
    (source / ".ananta" / "interactive-baseline").mkdir(parents=True, exist_ok=True)
    (source / ".ananta" / "interactive-baseline" / "app.py").write_text("BASELINE\n")
    for stale in ("system-prompt.md", "task-brief.md", "mutation-report.json", "last-final-answer.json", "review.json"):
        (source / ".ananta" / stale).write_text("{}")
    (source / "tests").mkdir(exist_ok=True)
    (source / "tests" / "app.py").write_text("def test_changed():\n    pass\n")
    (source / "README.md").write_text("changed by the worker\n")
    return source


def test_carry_keeps_changes_and_drops_per_attempt_scaffolding(tmp_path):
    source = _source_workspace(tmp_path)
    destination = tmp_path / "self-dev-new"
    baseline = {"README.md": "a" * 64, "tests/app.py": "b" * 64}

    result = carry_workspace(source, destination, baseline)

    assert result == baseline
    assert (destination / "README.md").read_text() == "changed by the worker\n"
    assert (destination / "tests" / "app.py").exists()
    assert (destination / ".ananta" / "interactive-baseline" / "app.py").read_text() == "BASELINE\n"
    for stale in ("system-prompt.md", "task-brief.md", "mutation-report.json", "last-final-answer.json", "review.json"):
        assert not (destination / ".ananta" / stale).exists(), stale


def test_carry_fails_closed(tmp_path):
    source = _source_workspace(tmp_path)
    with pytest.raises(ValueError, match="workspace_already_exists"):
        carry_workspace(source, source, {})
    with pytest.raises(ValueError, match="carry_source_missing"):
        carry_workspace(tmp_path / "nope", tmp_path / "dest", {})
    empty = tmp_path / "empty"
    (empty / ".ananta").mkdir(parents=True)
    with pytest.raises(ValueError, match="carry_source_empty"):
        carry_workspace(empty, tmp_path / "dest2", {})
    linked = _source_workspace(tmp_path)
    (linked / "link.py").symlink_to(linked / "README.md")
    with pytest.raises(ValueError, match="carry_symlink_denied"):
        carry_workspace(linked, tmp_path / "dest3", {})
