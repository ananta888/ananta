from __future__ import annotations

from agent.repository import task_repo


def normalize_text(text: str) -> str:
    return " ".join((text or "").strip().lower().split())


def followup_exists(parent_task_id: str, description: str) -> bool:
    norm = normalize_text(description)
    if not norm:
        return False
    for t in task_repo.get_all():
        if t.parent_task_id != parent_task_id:
            continue
        if normalize_text(t.description or "") == norm:
            return True
    return False


def normalize_depends_on(depends_on: list[str] | None, tid: str | None = None) -> list[str]:
    vals = []
    for item in (depends_on or []):
        if not item:
            continue
        dep = str(item).strip()
        if not dep:
            continue
        if tid and dep == tid:
            continue
        if dep not in vals:
            vals.append(dep)
    return vals


def effective_dependencies(task: dict) -> list[str]:
    deps = normalize_depends_on(task.get("depends_on"), tid=task.get("id"))
    parent = task.get("parent_task_id")
    if parent and parent not in deps and parent != task.get("id"):
        deps.append(parent)
    return deps


def _has_cycle(graph: dict[str, list[str]]) -> bool:
    state: dict[str, int] = {}

    def _dfs(node: str) -> bool:
        color = state.get(node, 0)
        if color == 1:
            return True
        if color == 2:
            return False
        state[node] = 1
        for nxt in graph.get(node, []):
            if nxt in graph and _dfs(nxt):
                return True
        state[node] = 2
        return False

    return any(_dfs(n) for n in graph if state.get(n, 0) == 0)


def validate_dependencies_and_cycles(tid: str, depends_on: list[str]) -> tuple[bool, str]:
    by_id = {t.id: t for t in task_repo.get_all()}
    missing = [d for d in depends_on if d not in by_id]
    if missing:
        return False, f"missing_dependencies:{','.join(missing)}"

    graph: dict[str, list[str]] = {}
    for task in by_id.values():
        task_dict = task.model_dump()
        graph[task.id] = effective_dependencies(task_dict)
    graph[tid] = normalize_depends_on(depends_on, tid=tid)
    if _has_cycle(graph):
        return False, "dependency_cycle_detected"
    return True, ""

