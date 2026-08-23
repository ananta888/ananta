"""Deterministic graph features derived only from evidenced relations."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any, Sequence


@dataclass(frozen=True, slots=True)
class GraphFeatures:
    node_id: str
    centrality: float
    query_distance: int | None
    evidence_refs: tuple[str, ...]
    coverage: str


def derive_graph_features(
    *,
    nodes: Sequence[dict[str, Any]],
    edges: Sequence[dict[str, Any]],
    query_node_ids: set[str],
) -> dict[str, GraphFeatures]:
    node_ids = sorted({str(node.get("id") or "") for node in nodes if str(node.get("id") or "")})
    neighbors: dict[str, set[str]] = defaultdict(set)
    refs: dict[str, set[str]] = defaultdict(set)
    valid_edge_count = 0
    for edge in edges:
        source = str(edge.get("source") or edge.get("from") or "")
        target = str(edge.get("target") or edge.get("to") or "")
        evidence = str(edge.get("source_ref") or edge.get("evidence_ref") or "")
        if source not in node_ids or target not in node_ids or not evidence:
            continue
        neighbors[source].add(target)
        neighbors[target].add(source)
        refs[source].add(evidence)
        refs[target].add(evidence)
        valid_edge_count += 1
    node_count = max(1, len(node_ids))
    ranks = {node_id: 1.0 / node_count for node_id in node_ids}
    damping = 0.85
    for _iteration in range(12):
        next_ranks = {node_id: (1.0 - damping) / node_count for node_id in node_ids}
        for source in node_ids:
            outgoing = sorted(neighbors[source])
            if not outgoing:
                share = damping * ranks[source] / node_count
                for target in node_ids:
                    next_ranks[target] += share
                continue
            share = damping * ranks[source] / len(outgoing)
            for target in outgoing:
                next_ranks[target] += share
        ranks = next_ranks
    max_rank = max(ranks.values(), default=1.0) or 1.0
    distances: dict[str, int] = {}
    queue = deque((node_id, 0) for node_id in sorted(query_node_ids & set(node_ids)))
    while queue:
        node_id, distance = queue.popleft()
        if node_id in distances:
            continue
        distances[node_id] = distance
        for neighbor in sorted(neighbors[node_id]):
            if neighbor not in distances:
                queue.append((neighbor, distance + 1))
    coverage = "complete" if valid_edge_count and all(refs[node_id] for node_id in node_ids) else "partial"
    return {
        node_id: GraphFeatures(
            node_id=node_id,
            centrality=ranks[node_id] / max_rank,
            query_distance=distances.get(node_id),
            evidence_refs=tuple(sorted(refs[node_id])),
            coverage=coverage,
        )
        for node_id in node_ids
    }
