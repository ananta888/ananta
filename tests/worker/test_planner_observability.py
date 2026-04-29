from __future__ import annotations

from worker.planning.plan_diff import build_plan_diff


def test_plan_diff_tracks_added_removed_and_reprioritized_steps() -> None:
    previous_plan = {"steps": [{"step_id": "a", "state": "done"}, {"step_id": "b", "state": "ready"}]}
    next_plan = {"steps": [{"step_id": "a", "state": "done"}, {"step_id": "c", "state": "ready"}]}
    diff = build_plan_diff(previous_plan=previous_plan, next_plan=next_plan, trigger="verification_failure", policy_decision_ref="p-1")
    assert diff["schema"] == "worker_plan_diff.v1"
    assert diff["added_steps"] == ["c"]
    assert diff["removed_steps"] == ["b"]
    assert diff["trigger"] == "verification_failure"

