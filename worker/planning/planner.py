from __future__ import annotations

from typing import Any

from worker.core.plan_artifact import build_plan_artifact
from worker.planning.step_graph import assert_acyclic, build_step_graph


def build_dependency_plan(
    *,
    task_id: str,
    profile: str,
    steps: list[dict[str, Any]],
) -> dict[str, Any]:
    graph = build_step_graph(steps=steps)
    assert_acyclic(graph)
    return build_plan_artifact(task_id=task_id, profile=profile, steps=[graph[key] for key in sorted(graph)])

