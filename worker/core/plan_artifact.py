from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

VALID_STEP_STATES = ("draft", "ready", "executing", "verifying", "done", "failed", "blocked")


@dataclass(frozen=True)
class PlanStep:
    step_id: str
    title: str
    depends_on: tuple[str, ...]
    state: str = "draft"
    required_tools: tuple[str, ...] = ()
    expected_artifacts: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "title": self.title,
            "depends_on": list(self.depends_on),
            "state": self.state,
            "required_tools": list(self.required_tools),
            "expected_artifacts": list(self.expected_artifacts),
        }


def build_plan_artifact(*, task_id: str, profile: str, steps: list[dict[str, Any]]) -> dict[str, Any]:
    normalized_steps: list[dict[str, Any]] = []
    for index, raw in enumerate(list(steps or []), start=1):
        state = str(raw.get("state") or "draft").strip().lower()
        if state not in VALID_STEP_STATES:
            state = "draft"
        plan_step = PlanStep(
            step_id=str(raw.get("step_id") or f"step-{index}").strip(),
            title=str(raw.get("title") or f"Step {index}").strip(),
            depends_on=tuple(str(item).strip() for item in list(raw.get("depends_on") or []) if str(item).strip()),
            state=state,
            required_tools=tuple(str(item).strip() for item in list(raw.get("required_tools") or []) if str(item).strip()),
            expected_artifacts=tuple(str(item).strip() for item in list(raw.get("expected_artifacts") or []) if str(item).strip()),
        )
        normalized_steps.append(plan_step.as_dict())
    return {
        "schema": "worker_plan_artifact.v1",
        "task_id": str(task_id).strip(),
        "profile": str(profile or "balanced").strip().lower() or "balanced",
        "steps": normalized_steps,
    }


def update_plan_step_state(*, plan_artifact: dict[str, Any], step_id: str, state: str) -> dict[str, Any]:
    normalized_state = str(state or "").strip().lower()
    if normalized_state not in VALID_STEP_STATES:
        raise ValueError(f"invalid_plan_step_state:{normalized_state or '<missing>'}")
    updated = deepcopy(dict(plan_artifact or {}))
    for step in list(updated.get("steps") or []):
        if str(step.get("step_id") or "") == str(step_id):
            step["state"] = normalized_state
            return updated
    raise ValueError(f"plan_step_not_found:{step_id}")

