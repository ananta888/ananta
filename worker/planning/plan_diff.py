from __future__ import annotations

from typing import Any


def build_plan_diff(*, previous_plan: dict[str, Any], next_plan: dict[str, Any], trigger: str, policy_decision_ref: str | None = None) -> dict[str, Any]:
    previous_steps = {str(step.get("step_id")): dict(step) for step in list(previous_plan.get("steps") or [])}
    next_steps = {str(step.get("step_id")): dict(step) for step in list(next_plan.get("steps") or [])}
    added = [step_id for step_id in next_steps if step_id not in previous_steps]
    removed = [step_id for step_id in previous_steps if step_id not in next_steps]
    reprioritized = [
        step_id
        for step_id in next_steps
        if step_id in previous_steps and str(next_steps[step_id].get("state") or "") != str(previous_steps[step_id].get("state") or "")
    ]
    return {
        "schema": "worker_plan_diff.v1",
        "trigger": str(trigger or "").strip() or "unknown",
        "policy_decision_ref": str(policy_decision_ref or "").strip() or None,
        "added_steps": sorted(added),
        "removed_steps": sorted(removed),
        "reprioritized_steps": sorted(reprioritized),
    }

