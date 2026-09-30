"""Pure graph helpers shared by topology patch evaluation and staging."""

from __future__ import annotations

import uuid
from typing import Any


def planned_node_id(organization_id: str, kind: str, key: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"ananta:organization-patch:{organization_id}:{kind}:{key}"))


def unit_subtree_ids(units: dict[str, dict[str, Any]], root_id: str) -> set[str]:
    result = {root_id}
    changed = True
    while changed:
        changed = False
        for unit_id, unit in units.items():
            if unit_id not in result and unit.get("parent_id") in result:
                result.add(unit_id)
                changed = True
    return result


def aggregate_subtree_activity(
    activity_by_unit: dict[str, dict[str, int]],
    subtree: set[str],
) -> dict[str, int]:
    activity_keys = {key for unit_id in subtree for key in activity_by_unit.get(unit_id, {})}
    return {
        key: sum(int(activity_by_unit.get(unit_id, {}).get(key, 0)) for unit_id in subtree)
        for key in sorted(activity_keys)
    }


def has_parent_cycle(units: dict[str, dict[str, Any]]) -> bool:
    for start in units:
        seen: set[str] = set()
        current = start
        while current in units:
            if current in seen:
                return True
            seen.add(current)
            current = str(units[current].get("parent_id") or "")
    return False


def has_relation_cycle(relations: list[dict[str, Any]]) -> bool:
    graph: dict[str, set[str]] = {}
    for row in relations:
        graph.setdefault(row["source_id"], set()).add(row["target_id"])
        graph.setdefault(row["target_id"], set())
    state: dict[str, int] = {}

    def visit(node: str) -> bool:
        if state.get(node) == 1:
            return True
        if state.get(node) == 2:
            return False
        state[node] = 1
        if any(visit(target) for target in graph.get(node, set())):
            return True
        state[node] = 2
        return False

    return any(visit(node) for node in sorted(graph) if not state.get(node))


__all__ = [
    "aggregate_subtree_activity",
    "has_parent_cycle",
    "has_relation_cycle",
    "planned_node_id",
    "unit_subtree_ids",
]
