from __future__ import annotations

from collections import deque
from typing import Any


def build_step_graph(*, steps: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    graph: dict[str, dict[str, Any]] = {}
    for step in list(steps or []):
        step_id = str(step.get("step_id") or "").strip()
        if not step_id:
            raise ValueError("step_graph_missing_step_id")
        if step_id in graph:
            raise ValueError(f"step_graph_duplicate_step:{step_id}")
        graph[step_id] = {
            "step_id": step_id,
            "depends_on": [str(item).strip() for item in list(step.get("depends_on") or []) if str(item).strip()],
            "required_tools": [str(item).strip() for item in list(step.get("required_tools") or []) if str(item).strip()],
            "expected_artifacts": [str(item).strip() for item in list(step.get("expected_artifacts") or []) if str(item).strip()],
            "state": str(step.get("state") or "draft").strip().lower() or "draft",
        }
    for node in graph.values():
        for dep in list(node["depends_on"]):
            if dep not in graph:
                raise ValueError(f"step_graph_missing_dependency:{node['step_id']}->{dep}")
    return graph


def assert_acyclic(graph: dict[str, dict[str, Any]]) -> None:
    indegree = {node: 0 for node in graph}
    for node in graph.values():
        for dep in node["depends_on"]:
            indegree[node["step_id"]] += 1
    queue = deque([node for node, degree in indegree.items() if degree == 0])
    visited = 0
    reverse: dict[str, list[str]] = {node: [] for node in graph}
    for node in graph.values():
        for dep in node["depends_on"]:
            reverse[dep].append(node["step_id"])
    while queue:
        node = queue.popleft()
        visited += 1
        for dependent in reverse[node]:
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                queue.append(dependent)
    if visited != len(graph):
        raise ValueError("step_graph_cycle_detected")


def ready_steps(*, graph: dict[str, dict[str, Any]]) -> list[str]:
    ready: list[str] = []
    for step_id, node in graph.items():
        state = str(node.get("state") or "draft")
        if state not in {"draft", "ready"}:
            continue
        deps = list(node.get("depends_on") or [])
        if all(str(graph[dep].get("state") or "") == "done" for dep in deps):
            ready.append(step_id)
    return sorted(ready)

