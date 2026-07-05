"""CRG-008: knowledge-gap analysis.

Finds:

* isolated relevant nodes (no in-edges, no out-edges within kind)
* untested hotspots (high degree but no ``covers`` edges)
* thin communities (small connected components)
* missing test edges (buildable_component without test in same scope)

Every recommendation references at least one node / edge, or carries
``insufficient_evidence`` (CCRIG-DD-007).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from worker.retrieval.codecompass_graph_store import CodeCompassGraphStore


KNOWLEDGE_GAPS_SCHEMA_VERSION = "knowledge_gaps.v1"
DEFAULT_HOTSPOT_THRESHOLD = 3
DEFAULT_THIN_COMPONENT_MAX = 2


@dataclass(frozen=True)
class KnowledgeGap:
    type: str
    severity: str
    nodes: tuple[str, ...]
    evidence_edges: tuple[dict[str, Any], ...] = ()
    rationale: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "severity": self.severity,
            "nodes": list(self.nodes),
            "evidence_edges": list(self.evidence_edges),
            "rationale": self.rationale,
        }


@dataclass(frozen=True)
class KnowledgeGapResult:
    schema_version: str
    gaps: tuple[KnowledgeGap, ...]
    summary: dict[str, int] = field(default_factory=dict)
    insufficient_evidence: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "gaps": [g.as_dict() for g in self.gaps],
            "summary": dict(self.summary),
            "insufficient_evidence": self.insufficient_evidence,
        }


def find_knowledge_gaps(
    *,
    graph_store: CodeCompassGraphStore,
    hotspot_degree_threshold: int = DEFAULT_HOTSPOT_THRESHOLD,
    thin_component_max: int = DEFAULT_THIN_COMPONENT_MAX,
) -> KnowledgeGapResult:
    payload = graph_store.load()
    nodes_by_id = (payload.get("node_index") or {}).get("by_id") or {}
    outgoing = payload.get("outgoing_index") or {}
    incoming = payload.get("incoming_index") or {}

    if not nodes_by_id:
        return KnowledgeGapResult(
            schema_version=KNOWLEDGE_GAPS_SCHEMA_VERSION,
            gaps=(),
            insufficient_evidence=True,
        )

    gaps: list[KnowledgeGap] = []

    # 1) isolated relevant nodes
    isolated: list[str] = []
    for nid in nodes_by_id:
        has_in = any((incoming.get(nid) or {}).values())
        has_out = any((outgoing.get(nid) or {}).values())
        if not has_in and not has_out:
            isolated.append(nid)
    if isolated:
        gaps.append(KnowledgeGap(
            type="isolated_node",
            severity="low",
            nodes=tuple(sorted(isolated)[:50]),
            rationale="node has neither in- nor out-edges in the symbolgraph",
        ))

# 2) untested hotspots: high degree + no 'covers' edge (in any direction)
    hotspots: list[tuple[str, int]] = []
    for nid in nodes_by_id:
        in_deg = sum(len(es) for es in (incoming.get(nid) or {}).values())
        out_deg = sum(len(es) for es in (outgoing.get(nid) or {}).values())
        deg = in_deg + out_deg
        if deg < hotspot_degree_threshold:
            continue
        # A node is "tested" if any incoming OR outgoing edge of type 'covers'
        # touches it.
        has_covers = any(
            edge_type == "covers"
            for edge_type in (incoming.get(nid) or {})
            for _ in (incoming.get(nid) or {}).get(edge_type, [])
        ) or any(
            edge_type == "covers"
            for edge_type in (outgoing.get(nid) or {})
            for _ in (outgoing.get(nid) or {}).get(edge_type, [])
        )
        if not has_covers:
            hotspots.append((nid, deg))
    if hotspots:
        hotspots.sort(key=lambda x: (-x[1], x[0]))
        gaps.append(KnowledgeGap(
            type="missing_test_coverage",
            severity="medium",
            nodes=tuple(nid for nid, _ in hotspots[:50]),
            rationale=("buildable_component or hot symbol without a 'covers' "
                       f"edge (degree >= {hotspot_degree_threshold})"),
        ))

    # 3) thin components: BFS over the outgoing direction, identify
    #    components with <= thin_component_max nodes.
    components = _connected_components(nodes_by_id, outgoing)
    thin = [c for c in components if 0 < len(c) <= thin_component_max]
    if thin:
        gaps.append(KnowledgeGap(
            type="thin_community",
            severity="low",
            nodes=tuple(sorted(n for c in thin for n in c)[:50]),
            rationale=f"connected component with <= {thin_component_max} nodes",
        ))

    summary = {
        "isolated": len(isolated),
        "untested_hotspots": len(hotspots),
        "thin_communities": len(thin),
        "total_nodes": len(nodes_by_id),
    }

    return KnowledgeGapResult(
        schema_version=KNOWLEDGE_GAPS_SCHEMA_VERSION,
        gaps=tuple(gaps),
        summary=summary,
        insufficient_evidence=False,
    )


def _connected_components(
    nodes_by_id: dict[str, Any],
    outgoing: dict[str, dict[str, list[Any]]],
) -> list[list[str]]:
    seen: set[str] = set()
    components: list[list[str]] = []
    for start in nodes_by_id:
        if start in seen:
            continue
        comp: list[str] = []
        stack = [start]
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            comp.append(cur)
            for entry in (outgoing.get(cur) or {}).values():
                for e in entry:
                    tgt = str(e.get("target_id") or "").strip()
                    if tgt and tgt not in seen:
                        stack.append(tgt)
        components.append(comp)
    return components


__all__ = [
    "KNOWLEDGE_GAPS_SCHEMA_VERSION",
    "KnowledgeGap",
    "KnowledgeGapResult",
    "find_knowledge_gaps",
]