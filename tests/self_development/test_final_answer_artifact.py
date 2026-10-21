"""``.ananta/last-final-answer.json``: one builder, both backends.

`collect_review` reads this artifact; without it a completed review fails with
`worker_review_answer_missing`. The mutation loop wrote it natively, the opencode
path did not write it at all — these tests pin the shared builder, the strict
verdict extraction and both proposal hooks.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from agent.services.self_development.final_answer_artifact import (
    build_final_answer_artifact,
    extract_verdict_answer,
    write_final_answer,
)

VERDICT = (
    '{"approved": true, "criteria_passed": [true, true], '
    '"findings": []}'
)


def test_builder_shapes_the_artifact_the_review_parses():
    artifact = build_final_answer_artifact(task_id="task-1", answer=VERDICT, session_id="sess-1")
    assert artifact["schema"] == "ananta_worker_final_answer.v1"
    assert artifact["task_id"] == "task-1"
    assert artifact["outcome"] == "final_answer"
    assert artifact["session_id"] == "sess-1"
    assert artifact["answer"] == VERDICT
    assert isinstance(artifact["created_at"], float)

    # bounded answer, like the loop's 16000 character ceiling
    long = build_final_answer_artifact(task_id="t", answer="x" * 40000)
    assert len(long["answer"]) == 16000


def test_extract_verdict_from_prose_uses_the_newest_object():
    answer = (
        "I reviewed both criteria.\n"
        'Result: {"approved": false, "criteria_passed": [false], "findings": ["missing test"]}'
        "\nFinal verdict: " + VERDICT
    )
    assert extract_verdict_answer(answer) == VERDICT


def test_extract_verdict_rejects_texts_that_are_not_verdicts():
    assert extract_verdict_answer("") is None
    assert extract_verdict_answer("All acceptance criteria verified: it works.") is None
    assert extract_verdict_answer('{"approved": "yes", "criteria_passed": [], "findings": []}') is None
    assert extract_verdict_answer('{"approved": true, "criteria_passed": true, "findings": []}') is None
    assert extract_verdict_answer('{"approved": true, "criteria_passed": [true]}') is None  # findings missing
    # unbalanced braces never become an answer
    assert extract_verdict_answer('{"approved": true, "criteria_passed": [true], "findings": [') is None


def test_written_artifact_passes_the_review_parser(tmp_path):
    from agent.services.self_development.review_workflow import parse_review_verdict

    assert write_final_answer(tmp_path, task_id="task-1", answer="verdict: " + VERDICT) is True

    artifact = json.loads((tmp_path / ".ananta" / "last-final-answer.json").read_text(encoding="utf-8"))
    assert artifact["outcome"] == "final_answer"
    result = parse_review_verdict(artifact["answer"], criteria_count=2)
    assert result.approved is True
    assert tuple(result.criteria_passed) == (True, True)


def test_writer_skips_without_a_verdict_and_never_overwrites(tmp_path):
    # no verdict in the text: nothing is written, the honest failure stays
    assert write_final_answer(tmp_path, task_id="t1", answer="looks fine to me") is False
    assert not (tmp_path / ".ananta" / "last-final-answer.json").exists()

    # the mutation loop's file is authoritative and must survive untouched
    loop_file = tmp_path / ".ananta" / "last-final-answer.json"
    loop_file.parent.mkdir(parents=True, exist_ok=True)
    loop_file.write_text(
        json.dumps(build_final_answer_artifact(task_id="t1", answer=VERDICT, session_id="loop")), encoding="utf-8"
    )
    before = loop_file.read_text(encoding="utf-8")
    assert write_final_answer(tmp_path, task_id="t1", answer=VERDICT) is False
    assert loop_file.read_text(encoding="utf-8") == before
    assert json.loads(before)["session_id"] == "loop"


def test_propose_hook_writes_the_artifact_after_a_successful_propose(monkeypatch, tmp_path):
    from agent.services import _task_scoped_propose_persist as mod

    calls = []
    monkeypatch.setattr(
        "agent.services.self_development.final_answer_artifact.write_final_answer",
        lambda workspace, *, task_id, answer, session_id="": calls.append((str(workspace), task_id, answer)) or True,
    )
    run = SimpleNamespace(workspace_dir=str(tmp_path), tid="task-1", raw_res="verdict: " + VERDICT, rc=0)
    mod._write_final_answer_for_proposal(run)
    assert calls == [(str(tmp_path), "task-1", "verdict: " + VERDICT)]

    calls.clear()
    run.rc = 1
    mod._write_final_answer_for_proposal(run)
    assert calls == []


def test_orchestrator_artifact_hook_uses_the_admission_workspace(monkeypatch, tmp_path):
    from agent.services import _task_scoped_propose_orch as orch

    calls = []
    monkeypatch.setattr(
        "agent.services.self_development.final_answer_artifact.write_final_answer",
        lambda workspace, *, task_id, answer, session_id="": calls.append((str(workspace), task_id)) or True,
    )
    generic = tmp_path / "self-dev-task-2"
    admission = tmp_path / "acceptance-review-task-2"
    workspace = SimpleNamespace(workspace_dir=generic)
    service = SimpleNamespace(resolve_workspace_context=lambda *, task: workspace)
    monkeypatch.setattr(
        "agent.services.worker_workspace_service.get_worker_workspace_service",
        lambda: service,
    )
    task_record = {"id": "task-2", "worker_execution_context": {"workspace": {"output_dir": str(admission)}}}

    orch._write_review_answer_artifact(task="task-2", task_record=task_record, answer="verdict: " + VERDICT)

    # collect_review reads the admission workspace — the artifact must land there
    assert calls == [(str(admission), "task-2")]
