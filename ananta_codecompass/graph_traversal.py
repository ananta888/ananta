"""Bounded, cycle-guarded traversals over a loaded CodeCompass graph payload."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ananta_codecompass.graph_indexes import edges_from_index

OutgoingEdgeLookup = Callable[[str, "set[str] | None"], list[dict[str, Any]]]


def traverse_breadth_first(
    payload: dict[str, Any],
    *,
    seed_ids: list[str],
    max_depth: int,
    max_nodes: int,
    allowed_edge_types: set[str] | None,
    outgoing_edges: OutgoingEdgeLookup,
) -> dict[str, Any]:
    by_id: dict[str, Any] = {
        **dict((payload.get("semantic_index") or {}).get("by_id") or {}),
        **dict((payload.get("node_index") or {}).get("by_id") or {}),
    }
    visited: set[str] = set()
    queue: list[tuple[str, int, list[dict[str, Any]]]] = []
    for seed in sorted({str(item).strip() for item in list(seed_ids or []) if str(item).strip()}):
        if seed in by_id:
            queue.append((seed, 0, []))
    selected_nodes: list[dict[str, Any]] = []
    selected_paths: list[dict[str, Any]] = []
    while queue and len(selected_nodes) < max(1, int(max_nodes)):
        node_id, depth, path = queue.pop(0)
        if node_id in visited:
            continue
        visited.add(node_id)
        node = dict(by_id.get(node_id) or {})
        if not node:
            continue
        selected_nodes.append(node)
        if path:
            selected_paths.append({"node_id": node_id, "path": path})
        if depth >= max(0, int(max_depth)):
            continue
        for edge in outgoing_edges(node_id, allowed_edge_types):
            target = str(edge.get("target_id") or "").strip()
            if not target or target in visited:
                continue
            queue.append((target, depth + 1, [*path, dict(edge)]))
    return {
        "nodes": selected_nodes,
        "paths": selected_paths,
        "cycle_guarded": True,
        "bounded": True,
    }


def neighbor_steps(
    payload: dict[str, Any],
    node_id: str,
    direction: str,
    allowed_edge_types: set[str] | None,
) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    if direction in {"outgoing", "both"}:
        for edge in edges_from_index(payload.get("outgoing_index") or {}, node_id, allowed_edge_types):
            other = str(edge.get("target_id") or "").strip()
            if other:
                steps.append({**edge, "direction_used": "outgoing", "_other_id": other})
    if direction in {"incoming", "both"}:
        for edge in edges_from_index(payload.get("incoming_index") or {}, node_id, allowed_edge_types):
            other = str(edge.get("source_id") or "").strip()
            if other:
                steps.append({**edge, "direction_used": "incoming", "_other_id": other})
    steps.sort(key=lambda item: (
        str(item.get("edge_type") or ""),
        str(item.get("_other_id") or ""),
        str(item.get("direction_used") or ""),
    ))
    return steps


def traverse_evidence_paths(
    payload: dict[str, Any],
    *,
    seed_ids: list[str],
    max_depth: int,
    max_nodes: int,
    allowed_edge_types: set[str] | None = None,
    direction: str = "outgoing",
    max_paths_per_node: int = 3,
) -> dict[str, Any]:
    direction_name = str(direction or "outgoing").strip().lower()
    if direction_name not in {"outgoing", "incoming", "both"}:
        direction_name = "outgoing"
    by_id = dict((payload.get("node_index") or {}).get("by_id") or {})
    seeds = sorted({
        str(item).strip()
        for item in list(seed_ids or [])
        if str(item).strip() and str(item).strip() in by_id
    })
    depth_cap = max(0, int(max_depth))
    node_cap = max(1, int(max_nodes))
    path_cap = max(1, int(max_paths_per_node))
    expansion_cap = node_cap * max(4, path_cap * 2)

    paths_by_node: dict[str, list[dict[str, Any]]] = {}
    discovery_order: list[str] = []
    cycle_count = 0
    expansions = 0
    truncated = False
    queue: list[tuple[str, int, tuple[dict[str, Any], ...], frozenset[str]]] = [
        (seed, 0, (), frozenset({seed})) for seed in seeds
    ]
    while queue:
        node_id, depth, path, on_path = queue.pop(0)
        if depth >= depth_cap:
            continue
        for step in neighbor_steps(payload, node_id, direction_name, allowed_edge_types):
            other_id = str(step.pop("_other_id"))
            if other_id in on_path:
                cycle_count += 1
                continue
            if other_id not in by_id:
                continue
            if expansions >= expansion_cap:
                truncated = True
                queue.clear()
                break
            new_path = (*path, dict(step))
            if other_id not in seeds:
                if other_id not in paths_by_node:
                    if len(discovery_order) >= node_cap:
                        truncated = True
                        continue
                    paths_by_node[other_id] = []
                    discovery_order.append(other_id)
                bucket = paths_by_node[other_id]
                if len(bucket) < path_cap:
                    bucket.append({"depth": depth + 1, "edges": [dict(edge) for edge in new_path]})
            expansions += 1
            queue.append((other_id, depth + 1, new_path, on_path | {other_id}))

    result_paths = [
        {
            "node_id": node_id,
            "depth": min(entry["depth"] for entry in paths_by_node[node_id]),
            "evidence_paths": list(paths_by_node[node_id]),
        }
        for node_id in discovery_order
        if paths_by_node.get(node_id)
    ]
    return {
        "seed_ids": seeds,
        "direction": direction_name,
        "nodes": [dict(by_id[node_id]) for node_id in [*seeds, *discovery_order] if node_id in by_id],
        "paths": result_paths,
        "cycle_guarded": True,
        "cycle_count": cycle_count,
        "bounded": True,
        "truncated": truncated,
        "expansions": expansions,
    }
