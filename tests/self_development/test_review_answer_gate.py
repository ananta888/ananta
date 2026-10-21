"""The ``gate_review`` answer gate: no completion without a verdict, bounded rounds.

The reviewer's ``final_answer`` tool is side-effect free, so before this gate a
reviewer could finish without handing in the verdict ``collect_review`` parses —
the single-shot review dispatch then failed with ``worker_review_answer_missing``.
These tests pin the worker-side gate (materialize / force / bound) and the hub-side
condition that defers ``collect_review`` for another round.
"""
from __future__ import annotations

import json

from agent.services.self_development.final_answer_artifact import (
    REVIEW_ANSWER_ROUNDS_MAX,
    bump_review_answer_round,
    enforce_review_answer_gate,
    review_answer_present,
    review_answer_retry_pending,
    review_answer_rounds,
)

VERDICT = '{"approved": true, "criteria_passed": [true], "findings": []}'
FINAL_ANSWER_REPORT = "Tool 'final_answer': Erfolg\nOutput: " + repr({"answer": VERDICT})


def test_gate_ignores_every_other_task_kind(tmp_path):
    assert enforce_review_answer_gate(
        task_kind="coding", workspace=tmp_path, task_id="t", output=FINAL_ANSWER_REPORT
    ) is None
    assert not (tmp_path / ".ananta").exists()
    assert review_answer_rounds(tmp_path) == 0


def test_gate_materializes_a_produced_verdict_and_completes(tmp_path):
    gate = enforce_review_answer_gate(
        task_kind="gate_review", workspace=tmp_path, task_id="task-1", output=FINAL_ANSWER_REPORT
    )
    assert gate is None
    assert review_answer_present(tmp_path) is True
    assert review_answer_rounds(tmp_path) == 0  # no round was ever refused

    from agent.services.self_development.review_workflow import parse_review_verdict

    artifact = json.loads((tmp_path / ".ananta" / "last-final-answer.json").read_text(encoding="utf-8"))
    assert parse_review_verdict(artifact["answer"], criteria_count=1).approved is True


def test_gate_materializes_when_the_command_report_follows_the_answer(tmp_path):
    """The executor joins command and tool parts with '---': the end-anchored
    report regex misses, the raw verdict scan must still materialize."""
    combined = FINAL_ANSWER_REPORT + "\n---\n7 passed in 0.12s"
    gate = enforce_review_answer_gate(
        task_kind="gate_review", workspace=tmp_path, task_id="task-1", output=combined
    )
    assert gate is None
    assert review_answer_present(tmp_path) is True
    assert review_answer_rounds(tmp_path) == 0


def test_gate_forces_bounded_further_rounds_without_an_answer(tmp_path):
    for expected_round in range(1, REVIEW_ANSWER_ROUNDS_MAX + 1):
        gate = enforce_review_answer_gate(
            task_kind="gate_review", workspace=tmp_path, task_id="task-1", output="I read the files. Done."
        )
        assert gate == {"status": "todo", "reason_code": "review_answer_required", "round": expected_round}
    # exhausted: completion falls through to the honest answer_missing downstream
    assert enforce_review_answer_gate(
        task_kind="gate_review", workspace=tmp_path, task_id="task-1", output="I read the files. Done."
    ) is None
    assert review_answer_present(tmp_path) is False


def test_gate_passes_when_the_loop_already_wrote_the_artifact(tmp_path):
    from agent.services.self_development.final_answer_artifact import write_final_answer

    assert write_final_answer(tmp_path, task_id="task-1", answer=VERDICT) is True
    gate = enforce_review_answer_gate(
        task_kind="gate_review", workspace=tmp_path, task_id="task-1", output=""
    )
    assert gate is None
    assert review_answer_rounds(tmp_path) == 0


def test_present_requires_the_final_answer_outcome(tmp_path):
    path = tmp_path / ".ananta" / "last-final-answer.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"outcome": "draft", "answer": VERDICT}), encoding="utf-8")
    assert review_answer_present(tmp_path) is False
    path.write_text(json.dumps({"outcome": "final_answer", "answer": VERDICT}), encoding="utf-8")
    assert review_answer_present(tmp_path) is True


def test_round_counter_tolerates_corruption_and_never_goes_negative(tmp_path):
    assert review_answer_rounds(tmp_path) == 0
    path = tmp_path / ".ananta" / "review-answer-rounds"
    path.parent.mkdir(parents=True)
    path.write_text("not-a-number", encoding="utf-8")
    assert review_answer_rounds(tmp_path) == 0
    path.write_text("-5", encoding="utf-8")
    assert review_answer_rounds(tmp_path) == 0
    assert bump_review_answer_round(tmp_path) == 1


def test_retry_pending_only_while_a_round_is_unanswered(tmp_path):
    assert review_answer_retry_pending(workspace=tmp_path) is False  # never bumped

    bump_review_answer_round(tmp_path)  # rounds = 1, no artifact
    assert review_answer_retry_pending(workspace=tmp_path) is True

    from agent.services.self_development.final_answer_artifact import write_final_answer

    assert write_final_answer(tmp_path, task_id="task-1", answer=VERDICT) is True
    assert review_answer_retry_pending(workspace=tmp_path) is False  # answered


def test_retry_pending_stops_when_rounds_are_exhausted(tmp_path):
    for _ in range(REVIEW_ANSWER_ROUNDS_MAX + 1):
        bump_review_answer_round(tmp_path)
    assert review_answer_rounds(tmp_path) == REVIEW_ANSWER_ROUNDS_MAX + 1
    assert review_answer_retry_pending(workspace=tmp_path) is False


def test_review_workspace_dir_prefers_the_admission_context():
    from agent.services.self_development.final_answer_artifact import review_workspace_dir

    task = {
        "worker_execution_context": {
            "workspace": {"output_dir": "/project-workspaces/acceptance-review-t1", "scope_key": "x"}
        }
    }
    assert review_workspace_dir(task, "/project-workspaces/self-dev-t1") == "/project-workspaces/acceptance-review-t1"
    # missing or empty context falls back to the generic workspace
    assert review_workspace_dir({}, "/project-workspaces/self-dev-t1") == "/project-workspaces/self-dev-t1"
    assert review_workspace_dir({"worker_execution_context": {}}, "/ws") == "/ws"

    class TaskRecord:
        worker_execution_context = {"workspace": {"output_dir": "/ws-object"}}

    assert review_workspace_dir(TaskRecord(), "/ws") == "/ws-object"
