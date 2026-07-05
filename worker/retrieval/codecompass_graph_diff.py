"""CRG-010: graph-diff between two CodeCompass snapshots.

Two snapshots are compared by ``content_hash``. The diff lists added,
removed, and changed nodes/edges. Manifest-Hash, file-Hash und
Parser-Version are kept in ``state/provenance`` for replay.

Works against the neutral CodeCompass graph contract — no CRG
installation required.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from worker.retrieval.codecompass_graph_store import CodeCompassGraphStore


GRAPH_DIFF_VERSION = "graph_diff.v1"


@dataclass(frozen=True)
class GraphDiff:
    schema_version: str
    base_snapshot_id: str
    target_snapshot_id: str
    added_node_ids: tuple[str, ...]
    removed_node_ids: tuple[str, ...]
    changed_node_ids: tuple[str, ...]
    added_edges: tuple[dict[str, Any], ...]
    removed_edges: tuple[dict[str, Any], ...]
    changed_edges: tuple[dict[str, Any], ...]
    base_provenance: dict[str, Any]
    target_provenance: dict[str, Any]
    warnings: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "base_snapshot_id": self.base_snapshot_id,
            "target_snapshot_id": self.target_snapshot_id,
            "added_node_ids": list(self.added_node_ids),
            "removed_node_ids": list(self.removed_node_ids),
            "changed_node_ids": list(self.changed_node_ids),
            "added_edges": list(self.added_edges),
            "removed_edges": list(self.removed_edges),
            "changed_edges": list(self.changed_edges),
            "base_provenance": dict(self.base_provenance),
            "target_provenance": dict(self.target_provenance),
            "warnings": list(self.warnings),
        }


def _snapshot_id(graph_store: CodeCompassGraphStore) -> str:
    payload = graph_store.load()
    return _compute_payload_hash(payload)


def _compute_payload_hash(payload: dict[str, Any]) -> str:
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def diff_snapshots(
    *,
    base: CodeCompassGraphStore,
    target: CodeCompassGraphStore,
    include_x86: bool = False,
    include_rig: bool = True,
) -> GraphDiff:
    """Diff the symbolgraph (and optionally RIG / x86) of two stores."""
    base_payload = base.load()
    target_payload = target.load()

    warnings: list[str] = []

    base_nodes = {n["id"]: n for n in (base_payload.get("nodes") or []) if isinstance(n, dict)}
    target_nodes = {n["id"]: n for n in (target_payload.get("nodes") or []) if isinstance(n, dict)}

    added_nodes = sorted(set(target_nodes) - set(base_nodes))
    removed_nodes = sorted(set(base_nodes) - set(target_nodes))

    changed_nodes: list[str] = []
    for nid in set(base_nodes) & set(target_nodes):
        if _signature(base_nodes[nid]) != _signature(target_nodes[nid]):
            changed_nodes.append(nid)
    changed_nodes.sort()

    # Edges
    base_edges, base_edge_set = _normalise_edges(base_payload.get("edges") or [])
    target_edges, target_edge_set = _normalise_edges(target_payload.get("edges") or [])
    added_e = sorted(target_edge_set - base_edge_set)
    removed_e = sorted(base_edge_set - target_edge_set)
    changed_e: list[dict[str, Any]] = []
    # "changed" edges = same endpoints/kind but different confidence / attrs
    base_by_key = {(e["source_id"], e["target_id"], e["edge_type"]): e
                   for e in base_edges}
    target_by_key = {(e["source_id"], e["target_id"], e["edge_type"]): e
                     for e in target_edges}
    for key in set(base_by_key) & set(target_by_key):
        if _edge_signature(base_by_key[key]) != _edge_signature(target_by_key[key]):
            changed_e.append({
                "before": base_by_key[key],
                "after": target_by_key[key],
            })

    added_edges_full = [target_by_key[k] for k in added_e]
    removed_edges_full = [base_by_key[k] for k in removed_e]

    base_prov = dict((base_payload.get("state") or {}))
    target_prov = dict((target_payload.get("state") or {}))

    return GraphDiff(
        schema_version=GRAPH_DIFF_VERSION,
        base_snapshot_id=_compute_payload_hash(base_payload),
        target_snapshot_id=_compute_payload_hash(target_payload),
        added_node_ids=tuple(added_nodes),
        removed_node_ids=tuple(removed_nodes),
        changed_node_ids=tuple(changed_nodes),
        added_edges=tuple(added_edges_full),
        removed_edges=tuple(removed_edges_full),
        changed_edges=tuple(changed_e),
        base_provenance=base_prov,
        target_provenance=target_prov,
        warnings=tuple(warnings),
    )


def _signature(node: dict[str, Any]) -> tuple:
    return (
        node.get("kind"),
        node.get("file"),
        node.get("name"),
    )


def _normalise_edges(edges: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], set[tuple[str, str, str]]]:
    out: list[dict[str, Any]] = []
    keys: set[tuple[str, str, str]] = set()
    for e in edges:
        if not isinstance(e, dict):
            continue
        sid = str(e.get("source_id") or "").strip()
        tid = str(e.get("target_id") or "").strip()
        kind = str(e.get("edge_type") or "").strip()
        if not (sid and tid and kind):
            continue
        out.append({"source_id": sid, "target_id": tid,
                    "edge_type": kind, **e})
        keys.add((sid, tid, kind))
    return out, keys


def _edge_signature(edge: dict[str, Any]) -> tuple:
    return (
        edge.get("confidence"),
        edge.get("heuristic"),
        edge.get("operation"),
    )


__all__ = [
    "GRAPH_DIFF_VERSION",
    "GraphDiff",
    "diff_snapshots",
]