"""Normalize CodeCompass graph records into a directed architecture graph."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Mapping


def project_graph(records: Mapping[str, Any] | None = None, *, nodes=None, edges=None) -> dict[str, Any]:
    payload = dict(records or {})
    raw_nodes = list(nodes if nodes is not None else payload.get("nodes") or [])
    raw_edges = list(edges if edges is not None else payload.get("edges") or [])
    projected_nodes = []
    for item in raw_nodes:
        if not isinstance(item, Mapping):
            continue
        node_id = str(item.get("id") or item.get("node_id") or "").strip()
        if not node_id:
            continue
        projected_nodes.append(
            {
                "id": node_id,
                "path": str(item.get("path") or item.get("file") or ""),
                "kind": str(item.get("kind") or item.get("type") or "unknown"),
                "title": str(item.get("title") or item.get("name") or node_id),
            }
        )
    projected_nodes.sort(key=lambda item: item["id"])
    known = {item["id"] for item in projected_nodes}
    adjacency: dict[str, set[str]] = defaultdict(set)
    incoming: dict[str, set[str]] = defaultdict(set)
    projected_edges = []
    for item in raw_edges:
        if not isinstance(item, Mapping):
            continue
        source = str(item.get("source") or item.get("from") or "").strip()
        target = str(item.get("target") or item.get("to") or "").strip()
        if source not in known or target not in known or source == target:
            continue
        relation = str(item.get("relation") or item.get("type") or "related")
        projected_edges.append({"source": source, "target": target, "relation": relation})
        adjacency[source].add(target)
        incoming[target].add(source)
    projected_edges.sort(key=lambda item: (item["source"], item["target"], item["relation"]))
    return {
        "nodes": projected_nodes,
        "edges": projected_edges,
        "adjacency": {key: sorted(value) for key, value in adjacency.items()},
        "incoming": {key: sorted(value) for key, value in incoming.items()},
    }
