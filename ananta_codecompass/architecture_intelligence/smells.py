"""Structural architecture smells over a projected graph."""

from __future__ import annotations

from typing import Any


def detect_cycles(projection: dict[str, Any]) -> list[list[str]]:
    adjacency = {key: list(value) for key, value in dict(projection.get("adjacency") or {}).items()}
    visiting: set[str] = set()
    visited: set[str] = set()
    stack: list[str] = []
    cycles: list[list[str]] = []

    def visit(node: str) -> None:
        visiting.add(node)
        stack.append(node)
        for nxt in adjacency.get(node, []):
            if nxt in visiting:
                start = stack.index(nxt)
                cycles.append(stack[start:] + [nxt])
            elif nxt not in visited:
                visit(nxt)
        stack.pop()
        visiting.remove(node)
        visited.add(node)

    for node in sorted(adjacency):
        if node not in visited:
            visit(node)
    unique = []
    seen = set()
    for cycle in cycles:
        key = tuple(sorted(cycle[:-1]))
        if key in seen:
            continue
        seen.add(key)
        unique.append(cycle)
    return unique[:20]


def detect_smells(projection: dict[str, Any], centrality: dict[str, Any]) -> list[dict[str, Any]]:
    smells: list[dict[str, Any]] = []
    for cycle in detect_cycles(projection):
        smells.append({"kind": "cycle", "nodes": cycle, "severity": "high"})
    for node in centrality.get("god_nodes") or []:
        smells.append({"kind": "god_node", "nodes": [node], "severity": "medium"})
    if len(centrality.get("bridges") or []) > max(3, len(projection.get("nodes") or []) // 4):
        smells.append(
            {
                "kind": "cross_community_churn",
                "nodes": [item["source"] for item in centrality["bridges"][:8]],
                "severity": "medium",
            }
        )
    return smells


def health_summary(smells: list[dict[str, Any]], node_count: int) -> dict[str, Any]:
    score = max(0, 100 - 15 * sum(1 for item in smells if item["severity"] == "high") - 7 * sum(1 for item in smells if item["severity"] == "medium"))
    return {"score": score, "smell_count": len(smells), "node_count": node_count, "status": "ok" if score >= 70 else "watch"}
