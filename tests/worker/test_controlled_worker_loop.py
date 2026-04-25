from __future__ import annotations

from worker.loop.budgets import WorkerLoopBudgets
from worker.loop.controlled_worker_loop import run_controlled_worker_loop
from worker.loop.progress_events import build_progress_event
from worker.loop.stop_conditions import should_stop_loop


def test_controlled_loop_completes_after_repair_iteration() -> None:
    result = run_controlled_worker_loop(
        task_id="AW-T33",
        trace_id="tr-33",
        context_hash="ctx-33",
        policy_decision="allow",
        approval_ref={"approval_id": "a-1", "status": "approved"},
        iteration_outcomes=[
            {"test_status": "failed", "made_progress": True},
            {"test_status": "passed", "made_progress": True},
        ],
        budgets=WorkerLoopBudgets(max_iterations=3, max_patch_attempts=3, max_runtime_seconds=120),
    )
    assert result["schema"] == "worker_loop_state.v1"
    assert result["status"] == "completed"
    assert result["stop_reason"] == "goal_reached"
    assert any(event["phase"] == "repair" for event in result["events"])


def test_controlled_loop_stops_on_missing_approval() -> None:
    result = run_controlled_worker_loop(
        task_id="AW-T33",
        trace_id="tr-33",
        context_hash="ctx-33",
        policy_decision="approval_required",
        approval_ref=None,
        iteration_outcomes=[{"test_status": "passed"}],
    )
    assert result["status"] == "stopped"
    assert result["stop_reason"] == "approval_required"


def test_budget_validation_and_stop_condition_helpers() -> None:
    event = build_progress_event(task_id="t", trace_id="tr", phase="plan", iteration=1, artifact_refs=["x"], detail="d")
    assert event["schema"] == "worker_progress_event.v1"
    assert should_stop_loop({"policy_state": "deny", "approval": None, "repeated_failure": False, "no_progress_detected": False, "budget_exhausted": False}) == (True, "policy_denied")
