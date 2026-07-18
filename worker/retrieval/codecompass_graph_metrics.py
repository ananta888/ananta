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

from dataclasses import dataclass
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


@dataclass(frozen=True)
class _BoundedShortestPathSummary:
    intermediate_counts: tuple[tuple[str, int], ...]
    paths_considered: int
    truncated: bool


def compute_graph_metrics(
    *,
    graph_store: CodeCompassGraphStore,
    top_k: int = 10,
) -> GraphMetricsResult:
    """Compute degree centrality + bounded betweenness approximation."""
    payload = graph_store.load()
    nodes_by_id = {
        **dict((payload.get("semantic_index") or {}).get("by_id") or {}),
        **dict((payload.get("node_index") or {}).get("by_id") or {}),
    }
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
            summary = _bounded_shortest_paths_through(
                a, b, outgoing, depth_cap=BETWEENNESS_DEPTH_CAP,
                path_cap=BETWEENNESS_PATH_CAP,
            )
            if summary.truncated and not path_cap_reached:
                path_cap_reached = True
                warnings.append("betweenness_path_cap_reached")
            for intermediate, count in summary.intermediate_counts:
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
) -> _BoundedShortestPathSummary:
    """Count intermediates on deterministic shortest paths within a bound.

    Only paths at the first target depth are considered. At most ``path_cap``
    shortest paths contribute; ``truncated`` explicitly reports further paths.
    Source and target are never returned as intermediates.
    """
    if src == tgt:
        return _BoundedShortestPathSummary((), 0, False)

    def _neighbours(node_id: str) -> list[str]:
        out: list[str] = []
        for entry in (outgoing.get(node_id) or {}).values():
            for edge in entry:
                t = str(edge.get("target_id") or "").strip()
                if t:
                    out.append(t)
        return sorted(out)

    distances: dict[str, int] = {src: 0}
    predecessors: dict[str, list[str]] = {src: []}
    queue = [src]
    target_depth: int | None = None
    while queue:
        current = queue.pop(0)
        current_depth = distances[current]
        if current_depth >= depth_cap:
            continue
        if target_depth is not None and current_depth + 1 > target_depth:
            continue
        for neighbour in _neighbours(current):
            next_depth = current_depth + 1
            known_depth = distances.get(neighbour)
            if known_depth is None:
                distances[neighbour] = next_depth
                predecessors[neighbour] = [current]
                if neighbour == tgt:
                    target_depth = next_depth
                else:
                    queue.append(neighbour)
            elif known_depth == next_depth:
                predecessors.setdefault(neighbour, []).append(current)

    if tgt not in distances:
        return _BoundedShortestPathSummary((), 0, False)

    counts: dict[str, int] = {}
    paths_considered = 0
    truncated = False

    def _collect(current: str, reverse_path: tuple[str, ...]) -> None:
        nonlocal paths_considered, truncated
        if truncated:
            return
        if current == src:
            if paths_considered >= path_cap:
                truncated = True
                return
            paths_considered += 1
            for intermediate in reverse_path:
                if intermediate not in {src, tgt}:
                    counts[intermediate] = counts.get(intermediate, 0) + 1
            return
        for predecessor in sorted(predecessors.get(current) or []):
            _collect(predecessor, (*reverse_path, predecessor))
            if truncated:
                return

    _collect(tgt, ())
    return _BoundedShortestPathSummary(
        tuple(sorted(counts.items())),
        paths_considered,
        truncated,
    )


__all__ = [
    "METRICS_SCHEMA_VERSION",
    "BETWEENNESS_DEPTH_CAP",
    "BETWEENNESS_PATH_CAP",
    "HubNode",
    "BridgeNode",
    "GraphMetricsResult",
    "compute_graph_metrics",
]
