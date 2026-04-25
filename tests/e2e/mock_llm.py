from __future__ import annotations

import re
from dataclasses import dataclass


def _slug(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.strip().lower())
    normalized = normalized.strip("-")
    return normalized or "goal"


@dataclass(frozen=True)
class MockLlmPlan:
    goal_id: str
    task_id: str
    task_title: str
    prompt: str


class MockLLM:
    """Deterministic LLM fixture for E2E dogfood tests."""

    def plan(self, goal: str) -> MockLlmPlan:
        slug = _slug(goal)
        return MockLlmPlan(
            goal_id=f"goal-{slug}",
            task_id=f"task-{slug}",
            task_title=f"Mock execution for {goal.strip()}",
            prompt=f"analyze-and-execute:{goal.strip()}",
        )
