"""Hub-owned task dependency normalization and validation policy."""

from __future__ import annotations

from agent.services.repository_registry import get_repository_registry


def _repos():
    return get_repository_registry()


def normalize_text(text: str) -> str:
    return " ".join((text or "").strip().lower().split())


def followup_exists(parent_task_id: str, description: str) -> bool:
    normalized = normalize_text(description)
    if not normalized:
        return False
    for task in _repos().task_repo.get_all():
        if task.parent_task_id != parent_task_id:
            continue
        if normalize_text(task.description or "") == normalized:
            return True
    return False


def normalize_depends_on(depends_on: list[str] | None, tid: str | None = None) -> list[str]:
    values: list[str] = []
    for item in depends_on or []:
        if not item:
            continue
        dependency = str(item).strip()
        if not dependency or (tid and dependency == tid):
            continue
        if dependency not in values:
            values.append(dependency)
    return values


def effective_dependencies(task: dict) -> list[str]:
    dependencies = normalize_depends_on(task.get("depends_on"), tid=task.get("id"))
    parent = task.get("parent_task_id")
    if parent and parent not in dependencies and parent != task.get("id"):
        dependencies.append(parent)
    return dependencies


def _has_cycle(graph: dict[str, list[str]]) -> bool:
    state: dict[str, int] = {}

    def visit(node: str) -> bool:
        color = state.get(node, 0)
        if color == 1:
            return True
        if color == 2:
            return False
        state[node] = 1
        for dependency in graph.get(node, []):
            if dependency in graph and visit(dependency):
                return True
        state[node] = 2
        return False

    return any(visit(node) for node in graph if state.get(node, 0) == 0)


def validate_dependency_graph(graph: dict[str, list[str]]) -> tuple[bool, str]:
    normalized_graph = {
        str(task_id): normalize_depends_on(dependencies, tid=str(task_id))
        for task_id, dependencies in (graph or {}).items()
    }
    if _has_cycle(normalized_graph):
        return False, "dependency_cycle_detected"
    return True, ""


def validate_dependencies_and_cycles(task_id: str, depends_on: list[str]) -> tuple[bool, str]:
    tasks_by_id = {task.id: task for task in _repos().task_repo.get_all()}
    missing = [dependency for dependency in depends_on if dependency not in tasks_by_id]
    if missing:
        return False, f"missing_dependencies:{','.join(missing)}"

    graph = {
        task.id: effective_dependencies(task.model_dump())
        for task in tasks_by_id.values()
    }
    graph[task_id] = normalize_depends_on(depends_on, tid=task_id)
    return validate_dependency_graph(graph)


__all__ = [
    "effective_dependencies",
    "followup_exists",
    "normalize_depends_on",
    "normalize_text",
    "validate_dependencies_and_cycles",
    "validate_dependency_graph",
]
