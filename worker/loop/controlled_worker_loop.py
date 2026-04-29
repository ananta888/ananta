from __future__ import annotations

import time
from typing import Any

from worker.core.execution_profile import normalize_execution_profile
from worker.core.plan_artifact import build_plan_artifact, update_plan_step_state
from worker.loop.budgets import WorkerLoopBudgets, budgets_for_profile
from worker.loop.progress_events import build_progress_event
from worker.loop.stop_conditions import should_stop_loop
from worker.planning.checkpoint_store import PlannerCheckpointStore


def run_controlled_worker_loop(
    *,
    task_id: str,
    trace_id: str,
    context_hash: str,
    policy_decision: str,
    approval_ref: dict[str, Any] | None,
    iteration_outcomes: list[dict[str, Any]],
    budgets: WorkerLoopBudgets | None = None,
    execution_profile: str | None = "balanced",
    checkpoint_store_path: str | None = None,
) -> dict[str, Any]:
    normalized_profile = normalize_execution_profile(execution_profile)
    bounded_budgets = budgets or budgets_for_profile(normalized_profile)
    bounded_budgets.validate()
    started = time.monotonic()
    state = {
        "schema": "worker_loop_state.v1",
        "task_id": str(task_id).strip(),
        "trace_id": str(trace_id).strip(),
        "context_hash": str(context_hash).strip(),
        "execution_profile": normalized_profile,
        "policy_state": str(policy_decision).strip().lower() or "allow",
        "phase": "observe",
        "iteration": 0,
        "patch_attempts": 0,
        "status": "running",
        "stop_reason": "",
        "artifacts": [],
        "events": [],
        "plan_artifact": build_plan_artifact(
            task_id=task_id,
            profile=normalized_profile,
            steps=[
                {"step_id": "plan", "title": "Plan bounded patch attempt", "state": "ready"},
                {"step_id": "patch", "title": "Propose patch artifact", "depends_on": ["plan"], "state": "draft"},
                {"step_id": "test", "title": "Execute test command", "depends_on": ["patch"], "state": "draft"},
                {"step_id": "summarize", "title": "Persist final outcome", "depends_on": ["test"], "state": "draft"},
            ],
        ),
    }
    checkpoint_store = PlannerCheckpointStore(path=checkpoint_store_path) if checkpoint_store_path else None
    state["events"].append(
        build_progress_event(
            task_id=task_id,
            trace_id=trace_id,
            phase="observe",
            iteration=0,
            artifact_refs=[],
            detail=f"Loop started (profile={normalized_profile}).",
        )
    )

    stop, reason = should_stop_loop(
        {
            "policy_state": state["policy_state"],
            "approval": approval_ref,
            "repeated_failure": False,
            "no_progress_detected": False,
            "budget_exhausted": False,
        }
    )
    if stop:
        state["status"] = "stopped"
        state["stop_reason"] = reason
        state["phase"] = "summarize"
        state["events"].append(
            build_progress_event(
                task_id=task_id,
                trace_id=trace_id,
                phase="summarize",
                iteration=0,
                artifact_refs=[],
                detail=f"Loop stopped before patching: {reason}.",
            )
        )
        return state

    no_progress_count = 0
    for iteration in range(1, bounded_budgets.max_iterations + 1):
        if (time.monotonic() - started) > bounded_budgets.max_runtime_seconds:
            state["status"] = "stopped"
            state["stop_reason"] = "budget_exhausted"
            break
        outcome = dict(iteration_outcomes[iteration - 1]) if iteration - 1 < len(iteration_outcomes) else {}
        state["iteration"] = iteration
        state["phase"] = "plan"
        state["plan_artifact"] = update_plan_step_state(
            plan_artifact=state["plan_artifact"],
            step_id="plan",
            state="executing",
        )
        state["events"].append(
            build_progress_event(
                task_id=task_id,
                trace_id=trace_id,
                phase="plan",
                iteration=iteration,
                detail="Planning bounded patch attempt.",
            )
        )

        state["phase"] = "patch"
        state["plan_artifact"] = update_plan_step_state(
            plan_artifact=state["plan_artifact"],
            step_id="patch",
            state="executing",
        )
        state["patch_attempts"] = int(state["patch_attempts"]) + 1
        patch_ref = str(outcome.get("patch_ref") or f"patch:iter-{iteration}")
        state["artifacts"].append(patch_ref)
        state["events"].append(
            build_progress_event(
                task_id=task_id,
                trace_id=trace_id,
                phase="patch",
                iteration=iteration,
                artifact_refs=[patch_ref],
                detail="Patch artifact proposed.",
            )
        )

        state["phase"] = "test"
        state["plan_artifact"] = update_plan_step_state(
            plan_artifact=state["plan_artifact"],
            step_id="test",
            state="executing",
        )
        test_status = str(outcome.get("test_status") or "failed").strip().lower()
        test_ref = str(outcome.get("test_ref") or f"test:iter-{iteration}")
        state["artifacts"].append(test_ref)
        state["events"].append(
            build_progress_event(
                task_id=task_id,
                trace_id=trace_id,
                phase="test",
                iteration=iteration,
                artifact_refs=[test_ref],
                detail=f"Test status={test_status}.",
            )
        )

        if test_status == "passed":
            state["phase"] = "summarize"
            state["plan_artifact"] = update_plan_step_state(
                plan_artifact=state["plan_artifact"],
                step_id="summarize",
                state="done",
            )
            state["status"] = "completed"
            state["stop_reason"] = "goal_reached"
            state["events"].append(
                build_progress_event(
                    task_id=task_id,
                    trace_id=trace_id,
                    phase="summarize",
                    iteration=iteration,
                    artifact_refs=[patch_ref, test_ref],
                    detail="Loop completed successfully.",
                )
            )
            return state

        state["phase"] = "inspect"
        state["events"].append(
            build_progress_event(
                task_id=task_id,
                trace_id=trace_id,
                phase="inspect",
                iteration=iteration,
                detail="Inspecting failure and preparing repair path.",
            )
        )

        made_progress = bool(outcome.get("made_progress", True))
        if not made_progress:
            no_progress_count += 1
        else:
            no_progress_count = 0
        repeated_failure = state["patch_attempts"] >= bounded_budgets.max_patch_attempts and test_status != "passed"
        no_progress_detected = no_progress_count >= 2
        stop, reason = should_stop_loop(
            {
                "policy_state": state["policy_state"],
                "approval": approval_ref,
                "repeated_failure": repeated_failure,
                "no_progress_detected": no_progress_detected,
                "budget_exhausted": False,
            }
        )
        if stop:
            state["status"] = "stopped"
            state["stop_reason"] = reason
            state["phase"] = "summarize"
            state["events"].append(
                build_progress_event(
                    task_id=task_id,
                    trace_id=trace_id,
                    phase="summarize",
                    iteration=iteration,
                    artifact_refs=[patch_ref, test_ref],
                    detail=f"Loop stopped: {reason}.",
                )
            )
            return state

        state["phase"] = "repair"
        repair_ref = str(outcome.get("repair_ref") or f"repair:iter-{iteration}")
        state["artifacts"].append(repair_ref)
        state["events"].append(
            build_progress_event(
                task_id=task_id,
                trace_id=trace_id,
                phase="repair",
                iteration=iteration,
                artifact_refs=[repair_ref],
                detail="Prepared repair artifact for next iteration.",
            )
        )
        if checkpoint_store is not None:
            checkpoint_store.save(
                payload={
                    "schema": "worker_planner_checkpoint.v1",
                    "task_id": state["task_id"],
                    "trace_id": state["trace_id"],
                    "completed_steps": [
                        step.get("step_id")
                        for step in list(state["plan_artifact"].get("steps") or [])
                        if str(step.get("state") or "") == "done"
                    ],
                    "pending_steps": [
                        step.get("step_id")
                        for step in list(state["plan_artifact"].get("steps") or [])
                        if str(step.get("state") or "") != "done"
                    ],
                    "budget_counters": {
                        "iterations_used": int(state["iteration"]),
                        "patch_attempts": int(state["patch_attempts"]),
                    },
                    "policy_snapshot_ref": f"policy:{state['policy_state']}",
                }
            )

    if state["status"] == "running":
        state["status"] = "stopped"
        state["stop_reason"] = "budget_exhausted"
        state["phase"] = "summarize"
        state["events"].append(
            build_progress_event(
                task_id=task_id,
                trace_id=trace_id,
                phase="summarize",
                iteration=int(state["iteration"]),
                artifact_refs=[],
                detail="Loop stopped due to iteration/runtime budgets.",
            )
        )
    return state
