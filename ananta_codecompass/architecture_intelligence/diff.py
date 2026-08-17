"""Deterministic graph and community diffs."""

from __future__ import annotations

from typing import Any, Mapping


def diff_graphs(old: Mapping[str, Any], new: Mapping[str, Any]) -> dict[str, Any]:
    old_nodes = {str(item.get("id") or item.get("node_id")) for item in list(old.get("nodes") or [])}
    new_nodes = {str(item.get("id") or item.get("node_id")) for item in list(new.get("nodes") or [])}
    old_edges = {
        (str(item.get("source")), str(item.get("target")), str(item.get("relation") or item.get("type") or ""))
        for item in list(old.get("edges") or [])
    }
    new_edges = {
        (str(item.get("source")), str(item.get("target")), str(item.get("relation") or item.get("type") or ""))
        for item in list(new.get("edges") or [])
    }
    added_nodes = sorted(new_nodes - old_nodes)
    removed_nodes = sorted(old_nodes - new_nodes)
    added_edges = sorted(new_edges - old_edges)
    removed_edges = sorted(old_edges - new_edges)
    classifier = "additive"
    if removed_nodes or removed_edges:
        classifier = "breaking_candidate"
    if added_nodes or added_edges:
        classifier = "growth" if classifier == "additive" else classifier
    if not added_nodes and not removed_nodes and not added_edges and not removed_edges:
        classifier = "unchanged"
    return {
        "schema": "codecompass.architecture-diff.v1",
        "added_nodes": added_nodes,
        "removed_nodes": removed_nodes,
        "added_edges": [{"source": a, "target": b, "relation": c} for a, b, c in added_edges],
        "removed_edges": [{"source": a, "target": b, "relation": c} for a, b, c in removed_edges],
        "classifier": classifier,
        "coverage_warning": "missing_coverage_must_not_mean_deleted_architecture",
    }
