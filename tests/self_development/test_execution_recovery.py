"""Salvaging finished workspace runs whose dispatch response was lost."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.services.self_development.coordinator import DevelopmentCoordinator
from agent.services.self_development.execution_recovery import completed_run_artifacts


def _report(outcome: str = "final_answer") -> dict:
    return {"schema": "ananta_worker_mutation_report.v1", "task_id": "t", "outcome": outcome,
            "iterations": [], "code_analysis": {"required": True, "status": "accepted"}}


def _answer() -> dict:
    return {"schema": "ananta_worker_final_answer.v1", "task_id": "t", "answer": "done"}


def _workspace(tmp_path: Path, outcome: str = "final_answer", answer: bool = True) -> Path:
    ananta = tmp_path / ".ananta"
    ananta.mkdir(parents=True, exist_ok=True)
    (ananta / "mutation-report.json").write_text(json.dumps(_report(outcome)), encoding="utf-8")
    if answer:
        (ananta / "last-final-answer.json").write_text(json.dumps(_answer()), encoding="utf-8")
    return tmp_path


def test_a_finished_run_exposes_its_durable_result(tmp_path):
    artifacts = completed_run_artifacts(_workspace(tmp_path))
    assert artifacts["report"]["outcome"] == "final_answer"
    assert artifacts["answer"]["answer"] == "done"


@pytest.mark.parametrize("outcome", ["max_iterations", "loop_aborted", "policy_blocked", ""])
def test_an_aborted_run_is_never_salvaged(tmp_path, outcome):
    assert completed_run_artifacts(_workspace(tmp_path, outcome=outcome)) is None


def test_a_missing_answer_or_report_is_never_salvaged(tmp_path):
    assert completed_run_artifacts(_workspace(tmp_path, answer=False)) is None
    assert completed_run_artifacts(tmp_path) is None
    broken = _workspace(tmp_path)
    (broken / ".ananta" / "mutation-report.json").write_text("not json", encoding="utf-8")
    assert completed_run_artifacts(broken) is None
    (broken / ".ananta" / "mutation-report.json").write_text(
        json.dumps({"schema": "other.v1", "outcome": "final_answer"}), encoding="utf-8")
    assert completed_run_artifacts(broken) is None


def test_the_recovery_replays_the_verification_and_completes_the_bookkeeping(tmp_path, monkeypatch):
    recorded = []
    monkeypatch.setattr("agent.services.self_development.coordinator.update_local_task_status",
                        lambda *a, **kw: recorded.append((a, kw)))
    workspace = _workspace(tmp_path)
    profile = {"command": "python3 -c 'raise SystemExit(0)'", "timeout_seconds": 30}

    assert DevelopmentCoordinator._recover_lost_dispatch(
        SimpleNamespace(), "task-1", "http://ai-agent-alpha:5000", profile, workspace) is True

    args, kwargs = recorded[0]
    assert args[:2] == ("task-1", "completed")
    assert kwargs["event_type"] == "artifact_first_completion"
    assert kwargs["last_exit_code"] == 0
    assert kwargs["last_proposal"]["command"] == profile["command"]


def test_the_recovery_refuses_a_workspace_that_does_not_verify(tmp_path, monkeypatch):
    recorded = []
    monkeypatch.setattr("agent.services.self_development.coordinator.update_local_task_status",
                        lambda *a, **kw: recorded.append((a, kw)))
    workspace = _workspace(tmp_path)
    failing = {"command": "python3 -c 'raise SystemExit(3)'", "timeout_seconds": 30}

    assert DevelopmentCoordinator._recover_lost_dispatch(
        SimpleNamespace(), "task-1", "http://ai-agent-alpha:5000", failing, workspace) is False
    assert recorded == []
    empty = tmp_path / "empty"
    empty.mkdir()
    assert DevelopmentCoordinator._recover_lost_dispatch(
        SimpleNamespace(), "task-1", "http://ai-agent-alpha:5000",
        {"command": "python3 -c 'raise SystemExit(0)'", "timeout_seconds": 30}, empty) is False
    assert recorded == []
