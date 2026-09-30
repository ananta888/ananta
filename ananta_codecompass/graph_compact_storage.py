"""Compact on-disk encoding of CodeCompass graph edges (``compact_v2``).

Derivable edge fields (edge identifiers, ``raw_edge_type`` and the default
provenance) are dropped on save and re-derived on load. Derived lookup
indexes are never persisted.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from ananta_codecompass.graph_edge_identity import derived_edge_id

COMPACT_STORAGE_ENCODING = "compact_v2"


def compact_storage_payload(payload: dict[str, Any]) -> dict[str, Any]:
    stored = dict(payload)
    state = dict(stored.get("state") or {})
    manifest_hash = str(state.get("manifest_hash") or "")
    role_edges = {
        "graph_edges": [
            dict(item) for item in list(stored.get("edges") or [])
            if isinstance(item, dict)
        ],
        "semantic_edges": [
            dict(item) for item in list(stored.get("semantic_edges") or [])
            if isinstance(item, dict)
        ],
    }
    edge_storage: dict[str, dict[str, Any]] = {}
    for role, edges in role_edges.items():
        raw_edge_type_derived = bool(edges) and all(
            "raw_edge_type" in edge for edge in edges
        )
        provenance_defaulted = bool(edges) and all(
            isinstance(edge.get("provenance"), dict)
            and str((edge.get("provenance") or {}).get("manifest_hash") or "")
            == manifest_hash
            and str((edge.get("provenance") or {}).get("output_kind") or "")
            == role
            for edge in edges
        )
        edge_ids_derived = bool(edges) and all(
            str(edge.get("edge_id") or "").strip() for edge in edges
        )
        derived_by_strategy = {
            strategy: [
                derived_edge_id(edge, strategy=strategy)
                for edge in edges
            ]
            for strategy in (
                "normalized",
                "legacy_confidence_default_omitted",
            )
        }
        strategy_scores: dict[str, int] = {}
        for strategy, candidates in derived_by_strategy.items():
            candidate_counts = Counter(candidates)
            strategy_scores[strategy] = sum(
                1
                for edge, candidate in zip(edges, candidates)
                if candidate
                and candidate_counts[candidate] == 1
                and str(edge.get("edge_id") or "") == candidate
            )
        edge_id_strategy = max(
            strategy_scores,
            key=lambda strategy: (strategy_scores[strategy], strategy == "normalized"),
        )
        derived_ids = derived_by_strategy[edge_id_strategy]
        identity_counts = Counter(derived_ids)
        compact_edges: list[dict[str, Any]] = []
        for edge, derived_id in zip(edges, derived_ids):
            compact = dict(edge)
            if (
                edge_ids_derived
                and derived_id
                and identity_counts[derived_id] == 1
                and str(compact.get("edge_id") or "") == derived_id
            ):
                compact.pop("edge_id", None)
            if (
                raw_edge_type_derived
                and compact.get("raw_edge_type") == compact.get("edge_type")
            ):
                compact.pop("raw_edge_type", None)
            if provenance_defaulted:
                provenance = dict(compact.get("provenance") or {})
                provenance.pop("manifest_hash", None)
                provenance.pop("output_kind", None)
                if provenance:
                    compact["provenance"] = provenance
                else:
                    compact.pop("provenance", None)
            compact_edges.append(compact)
        edge_storage[role] = {
            "edge_ids_derived": edge_ids_derived,
            "edge_id_strategy": edge_id_strategy,
            "raw_edge_type_derived": raw_edge_type_derived,
            "provenance_defaulted": provenance_defaulted,
        }
        stored["edges" if role == "graph_edges" else "semantic_edges"] = compact_edges

    for key in ("node_index", "semantic_index", "outgoing_index", "incoming_index"):
        stored.pop(key, None)
    state.update({
        "edge_index_encoding": "derived",
        "edge_storage": edge_storage,
        "storage_encoding": COMPACT_STORAGE_ENCODING,
    })
    stored["state"] = state
    return stored


def hydrate_stored_edges(
    raw_edges: Any,
    *,
    state: dict[str, Any],
    role: str,
) -> list[dict[str, Any]]:
    settings = dict((state.get("edge_storage") or {}).get(role) or {})
    manifest_hash = str(state.get("manifest_hash") or "")
    edges: list[dict[str, Any]] = []
    for raw in list(raw_edges or []):
        if not isinstance(raw, dict):
            continue
        edge = dict(raw)
        if settings.get("raw_edge_type_derived"):
            edge.setdefault("raw_edge_type", edge.get("edge_type") or "related")
        if settings.get("provenance_defaulted"):
            provenance = dict(edge.get("provenance") or {})
            provenance.setdefault("manifest_hash", manifest_hash)
            provenance.setdefault("output_kind", role)
            edge["provenance"] = provenance
        if settings.get("edge_ids_derived") and not str(edge.get("edge_id") or "").strip():
            edge["edge_id"] = derived_edge_id(
                edge,
                strategy=str(settings.get("edge_id_strategy") or "normalized"),
            )
        edges.append(edge)
    return edges
