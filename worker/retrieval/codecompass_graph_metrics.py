"""CRG-007: hub and bridge detection over the symbolgraph.

Computes:

* ``degree_centrality`` (exact, normalised in/out)
* a *bounded, deterministic* betweenness approximation: only counts
  shortest paths up to a depth limit, never executes the full O(V*E)
  Brandes algorithm. This keeps the implementation dependency-free
  while remaining useful for "which node bridges two clusters".

Results carry stable IDs, scores, reasons, and evidence edges. The
Graph Viewer can read them as ``metadata`` without renderer-specific
logic (CCRIG-DD-006).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from worker.retrieval.codecompass_graph_store import CodeCompassGraphStore


METRICS_SCHEMA_VERSION = "graph_metrics.v1"
BETWEENNESS_DEPTH_CAP = 4
BETWEENNESS_PATH_CAP = 1000


@dataclass(frozen=True)
class HubNode:
    node_id: str
    kind: str
    score: float
    reasons: tuple[str, ...]
    evidence_edges: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class BridgeNode:
    node_id: str
    kind: str
    score: float
    reasons: tuple[str, ...]
    evidence_edges: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class GraphMetricsResult:
    schema_version: str
    hub_nodes: tuple[HubNode, ...]
    bridge_nodes: tuple[BridgeNode, ...]
    warnings: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "hub_nodes": [
                {"node_id": h.node_id, "kind": h.kind, "score": h.score,
                 "reasons": list(h.reasons),
                 "evidence_edges": list(h.evidence_edges)}
                for h in self.hub_nodes
            ],
            "bridge_nodes": [
                {"node_id": b.node_id, "kind": b.kind, "score": b.score,
                 "reasons": list(b.reasons),
                 "evidence_edges": list(b.evidence_edges)}
                for b in self.bridge_nodes
            ],
            "warnings": list(self.warnings),
        }


def compute_graph_metrics(
    *,
    graph_store: CodeCompassGraphStore,
    top_k: int = 10,
) -> GraphMetricsResult:
    """Compute degree centrality + bounded betweenness approximation."""
    payload = graph_store.load()
    nodes_by_id = (payload.get("node_index") or {}).get("by_id") or {}
    outgoing = payload.get("outgoing_index") or {}
    incoming = payload.get("incoming_index") or {}

    n = max(1, len(nodes_by_id))
    warnings: list[str] = []

    hub_scores: list[tuple[str, float]] = []
    for nid in nodes_by_id:
        out_deg = sum(len(edges) for edges in (outgoing.get(nid) or {}).values())
        in_deg = sum(len(edges) for edges in (incoming.get(nid) or {}).values())
        norm = (out_deg + in_deg) / max(1.0, n - 1)
        hub_scores.append((nid, norm))

    hub_scores.sort(key=lambda x: (-x[1], x[0]))
    top_hubs = hub_scores[:top_k]
    hub_nodes = tuple(
        HubNode(
            node_id=nid,
            kind=str((nodes_by_id.get(nid) or {}).get("kind") or "unknown"),
            score=round(score, 4),
            reasons=("high_combined_degree",),
            evidence_edges=(),
        )
        for nid, score in top_hubs
    )

    # Bounded betweenness: for each pair (a,b) of *top hubs*, walk BFS
    # up to BETWEENNESS_DEPTH_CAP and count paths that pass through c.
    bridge_counts: dict[str, int] = {}
    pairs_considered = 0
    path_cap_reached = False
    top_hub_ids = [nid for nid, _ in top_hubs[: max(2, top_k // 2)]]
    for i, a in enumerate(top_hub_ids):
        for b in top_hub_ids[i + 1:]:
            if a == b:
                continue
            pairs_considered += 1
            for intermediate, count in _bounded_shortest_paths_through(
                a, b, outgoing, depth_cap=BETWEENNESS_DEPTH_CAP,
                path_cap=BETWEENNESS_PATH_CAP,
            ):
                if path_cap_reached:
                    break
                if count >= BETWEENNESS_PATH_CAP:
                    path_cap_reached = True
                    warnings.append("betweenness_path_cap_reached")
                bridge_counts[intermediate] = bridge_counts.get(intermediate, 0) + count
    bridge_sorted = sorted(bridge_counts.items(), key=lambda x: (-x[1], x[0]))[:top_k]
    bridge_nodes = tuple(
        BridgeNode(
            node_id=nid,
            kind=str((nodes_by_id.get(nid) or {}).get("kind") or "unknown"),
            score=round(min(1.0, count / max(1, pairs_considered)), 4),
            reasons=("between_shortest_paths",),
            evidence_edges=(),
        )
        for nid, count in bridge_sorted
    )

    return GraphMetricsResult(
        schema_version=METRICS_SCHEMA_VERSION,
        hub_nodes=hub_nodes,
        bridge_nodes=bridge_nodes,
        warnings=tuple(warnings),
    )


def _bounded_shortest_paths_through(
    src: str,
    tgt: str,
    outgoing: dict[str, dict[str, list[Any]]],
    *,
    depth_cap: int,
    path_cap: int,
) -> list[tuple[str, int]]:
    """Return ``(intermediate_node, count)`` pairs for paths src -> tgt
    of length at most ``depth_cap``. Intermediate nodes (not src, not
    tgt) get counted. The function never produces more than
    ``path_cap`` paths overall.
    """
    if src == tgt:
        return []

    results: list[tuple[str, int]] = []
    path_total = 0

    def _neighbours(node_id: str) -> list[str]:
        out: list[str] = []
        for entry in (outgoing.get(node_id) or {}).values():
            for edge in entry:
                t = str(edge.get("target_id") or "").strip()
                if t:
                    out.append(t)
        return out

    def _dfs(current: str, depth: int, visited: set[str],
             intermediates: tuple[str, ...]) -> None:
        nonlocal path_total
        if path_total >= path_cap:
            return
        if current == tgt:
            for n in intermediates:
                results.append((n, 1))
            path_total += 1
            return
        if depth >= depth_cap:
            return
        for nb in _neighbours(current):
            if nb in visited:
                continue
            new_inter = intermediates + (current,) if current not in intermediates else intermediates
            _dfs(nb, depth + 1, visited | {nb}, new_inter)

    _dfs(src, 0, {src}, ())
    return results


__all__ = [
    "METRICS_SCHEMA_VERSION",
    "BETWEENNESS_DEPTH_CAP",
    "BETWEENNESS_PATH_CAP",
    "HubNode",
    "BridgeNode",
    "GraphMetricsResult",
    "compute_graph_metrics",
]