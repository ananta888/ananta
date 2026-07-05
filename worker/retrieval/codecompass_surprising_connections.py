"""CRG-009: surprising connections / unexpected coupling.

Marks edges as *surprise candidates* when they cross documented
boundaries (cross-domain, cross-layer, cross-language). The detection
uses node ``role_labels`` / ``domain`` attributes set by the importer
or by hand-written fixtures; it does not rely on bare name heuristics.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from worker.retrieval.codecompass_graph_store import CodeCompassGraphStore


SURPRISING_CONNECTIONS_SCHEMA_VERSION = "surprising_connections.v1"


@dataclass(frozen=True)
class SurprisingConnection:
    edge_kind: str
    from_node: str
    to_node: str
    score: float
    reason: str
    confidence: float
    confidence_kind: str
    from_attrs: dict[str, Any] = field(default_factory=dict)
    to_attrs: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "edge_kind": self.edge_kind,
            "from_node": self.from_node,
            "to_node": self.to_node,
            "score": self.score,
            "reason": self.reason,
            "confidence": self.confidence,
            "confidence_kind": self.confidence_kind,
            "from_attrs": dict(self.from_attrs),
            "to_attrs": dict(self.to_attrs),
        }


@dataclass(frozen=True)
class SurprisingConnectionsResult:
    schema_version: str
    candidates: tuple[SurprisingConnection, ...]
    warnings: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "candidates": [c.as_dict() for c in self.candidates],
            "warnings": list(self.warnings),
        }


def _attrs_of(node: dict[str, Any]) -> dict[str, Any]:
    # 1) Explicit ``attrs`` field if the store preserved it.
    attrs = node.get("attrs")
    if isinstance(attrs, dict) and attrs:
        return dict(attrs)
    # 2) Promote top-level domain/layer/language if present (these are
    #    the only fields CRG-009 cares about).
    promoted: dict[str, Any] = {}
    for key in ("domain", "layer", "language", "role_labels"):
        if key in node and node[key] is not None:
            promoted[key] = node[key]
    if promoted:
        return promoted
    # 3) Fall back to source_record (which carries the original record).
    sr = node.get("source_record")
    if isinstance(sr, dict):
        sr_attrs = sr.get("attrs")
        if isinstance(sr_attrs, dict):
            return dict(sr_attrs)
    return {}


def find_surprising_connections(
    *,
    graph_store: CodeCompassGraphStore,
    max_results: int = 50,
) -> SurprisingConnectionsResult:
    payload = graph_store.load()
    nodes_by_id = (payload.get("node_index") or {}).get("by_id") or {}
    outgoing = payload.get("outgoing_index") or {}
    warnings: list[str] = []

    candidates: list[SurprisingConnection] = []
    for src_id, edge_type_dict in outgoing.items():
        src_node = nodes_by_id.get(src_id) or {}
        src_attrs = _attrs_of(src_node)
        src_domain = str(src_attrs.get("domain") or "").strip()
        src_layer = str(src_attrs.get("layer") or "").strip()
        src_language = str(src_attrs.get("language") or "").strip()
        for edge_type, edges in edge_type_dict.items():
            for edge in edges:
                tgt_id = str(edge.get("target_id") or "").strip()
                if not tgt_id:
                    continue
                tgt_node = nodes_by_id.get(tgt_id) or {}
                tgt_attrs = _attrs_of(tgt_node)
                tgt_domain = str(tgt_attrs.get("domain") or "").strip()
                tgt_layer = str(tgt_attrs.get("layer") or "").strip()
                tgt_language = str(tgt_attrs.get("language") or "").strip()

                reasons: list[str] = []
                score = 0.0
                if src_domain and tgt_domain and src_domain != tgt_domain:
                    reasons.append("cross_domain")
                    score += 0.5
                if src_layer and tgt_layer and src_layer != tgt_layer:
                    reasons.append("cross_layer")
                    score += 0.3
                if src_language and tgt_language and src_language != tgt_language:
                    reasons.append("cross_language")
                    score += 0.2
                if not reasons:
                    continue
                # confidence / confidence_kind live on the edge record;
                # the store's top-level edge dict carries ``confidence``
                # but not ``confidence_kind`` (kept in source_record).
                confidence_kind = (
                    str(edge.get("confidence_kind") or "").upper()
                )
                if not confidence_kind:
                    sr = edge.get("source_record") or {}
                    confidence_kind = str(sr.get("confidence_kind") or "EXTRACTED").upper()
                confidence_raw = edge.get("confidence")
                try:
                    confidence = float(confidence_raw)
                except (TypeError, ValueError):
                    confidence = 0.5
                candidates.append(SurprisingConnection(
                    edge_kind=edge_type,
                    from_node=src_id,
                    to_node=tgt_id,
                    score=round(min(1.0, score), 4),
                    reason="+".join(reasons),
                    confidence=round(confidence, 4),
                    confidence_kind=confidence_kind,
                    from_attrs=src_attrs,
                    to_attrs=tgt_attrs,
                ))

    if len(candidates) > max_results:
        warnings.append("max_results_truncated")
        candidates = candidates[:max_results]

    candidates.sort(key=lambda c: (-c.score, c.from_node, c.to_node))
    return SurprisingConnectionsResult(
        schema_version=SURPRISING_CONNECTIONS_SCHEMA_VERSION,
        candidates=tuple(candidates),
        warnings=tuple(warnings),
    )


__all__ = [
    "SURPRISING_CONNECTIONS_SCHEMA_VERSION",
    "SurprisingConnection",
    "SurprisingConnectionsResult",
    "find_surprising_connections",
]