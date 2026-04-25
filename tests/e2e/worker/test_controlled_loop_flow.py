from __future__ import annotations

from worker.loop.budgets import WorkerLoopBudgets
from worker.loop.controlled_worker_loop import run_controlled_worker_loop


def test_controlled_loop_flow_stops_with_budget_and_policy_constraints() -> None:
    denied = run_controlled_worker_loop(
        task_id="AW-T36",
        trace_id="tr-denied",
        context_hash="ctx-36",
        policy_decision="deny",
        approval_ref=None,
        iteration_outcomes=[{"test_status": "failed"}],
    )
    assert denied["status"] == "stopped"
    assert denied["stop_reason"] == "policy_denied"

    repaired = run_controlled_worker_loop(
        task_id="AW-T36",
        trace_id="tr-success",
        context_hash="ctx-36",
        policy_decision="allow",
        approval_ref={"approval_id": "a-36", "status": "approved"},
        iteration_outcomes=[
            {"test_status": "failed", "made_progress": True, "repair_ref": "repair:1"},
            {"test_status": "passed", "made_progress": True},
        ],
        budgets=WorkerLoopBudgets(max_iterations=3, max_patch_attempts=3, max_runtime_seconds=120),
    )
    assert repaired["status"] == "completed"
    assert repaired["stop_reason"] == "goal_reached"
    assert any(event["phase"] == "repair" for event in repaired["events"])
