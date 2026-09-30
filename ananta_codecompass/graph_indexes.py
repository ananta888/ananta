"""Derived lookup indexes over CodeCompass graph nodes and edges."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

NodeIndexBuilder = Callable[[list[dict[str, Any]]], dict[str, Any]]
SemanticIndexBuilder = Callable[
    [list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]],
    dict[str, Any],
]
EdgeIndexBuilder = Callable[[list[dict[str, Any]]], tuple[dict[str, Any], dict[str, Any]]]


def build_node_index(nodes: list[dict[str, Any]]) -> dict[str, Any]:
    by_id = {}
    by_file: dict[str, list[str]] = {}
    by_kind: dict[str, list[str]] = {}
    by_name: dict[str, list[str]] = {}
    by_record_id: dict[str, list[str]] = {}
    for node in nodes:
        node_id = str(node.get("id") or "").strip()
        if not node_id:
            continue
        by_id[node_id] = dict(node)
        file = str(node.get("file") or "").strip()
        kind = str(node.get("kind") or "").strip().lower()
        name = str(node.get("name") or "").strip()
        record_id = str(node.get("record_id") or "").strip()
        if file:
            by_file.setdefault(file, []).append(node_id)
        if kind:
            by_kind.setdefault(kind, []).append(node_id)
        if name:
            by_name.setdefault(name, []).append(node_id)
        if record_id:
            by_record_id.setdefault(record_id, []).append(node_id)
    return {
        "by_id": by_id,
        "by_file": {key: sorted(value) for key, value in by_file.items()},
        "by_kind": {key: sorted(value) for key, value in by_kind.items()},
        "by_name": {key: sorted(value) for key, value in by_name.items()},
        "by_record_id": {key: sorted(value) for key, value in by_record_id.items()},
    }


def build_semantic_index(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    rules: list[dict[str, Any]],
) -> dict[str, Any]:
    by_id: dict[str, dict[str, Any]] = {}
    by_file: dict[str, list[str]] = {}
    by_kind: dict[str, list[str]] = {}
    by_language: dict[str, list[str]] = {}
    by_symbol: dict[str, list[str]] = {}
    by_semantic_kind: dict[str, list[str]] = {}
    by_rule_id: dict[str, list[str]] = {}
    for node in nodes:
        node_id = str(node.get("id") or "").strip()
        if not node_id:
            continue
        by_id[node_id] = dict(node)
        for key, bucket, transform in [
            ("file", by_file, str),
            ("kind", by_kind, lambda value: str(value).lower()),
            ("language", by_language, lambda value: str(value).lower()),
            ("symbol", by_symbol, str),
            ("semantic_kind", by_semantic_kind, lambda value: str(value).lower()),
            ("rule_id", by_rule_id, str),
        ]:
            value = transform(node.get(key) or "").strip()
            if value:
                bucket.setdefault(value, []).append(node_id)
    for edge in edges:
        rule_id = str(edge.get("rule_id") or "").strip()
        if rule_id:
            by_rule_id.setdefault(rule_id, []).append(f"{edge.get('source_id')}->{edge.get('target_id')}")
    for rule in rules:
        rule_id = str(rule.get("rule_id") or "").strip()
        if rule_id:
            by_rule_id.setdefault(rule_id, []).append(rule_id)
    return {
        "by_id": by_id,
        "by_file": {key: sorted(set(value)) for key, value in by_file.items()},
        "by_kind": {key: sorted(set(value)) for key, value in by_kind.items()},
        "by_language": {key: sorted(set(value)) for key, value in by_language.items()},
        "by_symbol": {key: sorted(set(value)) for key, value in by_symbol.items()},
        "by_semantic_kind": {key: sorted(set(value)) for key, value in by_semantic_kind.items()},
        "by_rule_id": {key: sorted(set(value)) for key, value in by_rule_id.items()},
    }


def build_edge_id_indexes(edges: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build outgoing/incoming indexes that reference edges by ``edge_id``."""

    outgoing: dict[str, dict[str, list[str]]] = {}
    incoming: dict[str, dict[str, list[str]]] = {}
    for edge in edges:
        edge_id = str(edge.get("edge_id") or "").strip()
        source_id = str(edge.get("source_id") or "").strip()
        target_id = str(edge.get("target_id") or "").strip()
        edge_type = str(edge.get("edge_type") or "related").strip().lower() or "related"
        if not edge_id or not source_id or not target_id:
            continue
        outgoing.setdefault(source_id, {}).setdefault(edge_type, []).append(edge_id)
        incoming.setdefault(target_id, {}).setdefault(edge_type, []).append(edge_id)
    return outgoing, incoming


def hydrate_edge_index(
    raw_index: Any,
    edge_lookup: dict[str, dict[str, Any]],
) -> dict[str, dict[str, list[dict[str, Any]]]]:
    if not isinstance(raw_index, dict):
        return {}
    hydrated: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for node_id, raw_bucket in raw_index.items():
        if not isinstance(raw_bucket, dict):
            continue
        bucket: dict[str, list[dict[str, Any]]] = {}
        for edge_type, raw_entries in raw_bucket.items():
            if not isinstance(raw_entries, list):
                continue
            entries: list[dict[str, Any]] = []
            for raw_entry in raw_entries:
                if isinstance(raw_entry, dict):
                    entries.append(dict(raw_entry))
                elif isinstance(raw_entry, str):
                    edge = edge_lookup.get(raw_entry)
                    if edge is not None:
                        entries.append(dict(edge))
            if entries:
                bucket[str(edge_type)] = entries
        if bucket:
            hydrated[str(node_id)] = bucket
    return hydrated


def edges_from_index(
    index: dict[str, Any],
    node_id: str,
    allowed_edge_types: set[str] | None,
) -> list[dict[str, Any]]:
    bucket = dict(index or {}).get(str(node_id), {})
    rows: list[dict[str, Any]] = []
    allow = {str(item).strip().lower() for item in set(allowed_edge_types or set()) if str(item).strip()}
    for edge_type in sorted(bucket):
        if allow and edge_type not in allow:
            continue
        rows.extend(dict(item) for item in list(bucket.get(edge_type) or []) if isinstance(item, dict))
    return rows


@dataclass(frozen=True)
class GraphIndexBuilders:
    """The index strategies a graph payload is assembled with.

    Stores pass their own (possibly overridden) builders so that subclasses
    such as the SQLite store keep their historic index contract.
    """

    node_index: NodeIndexBuilder = build_node_index
    semantic_index: SemanticIndexBuilder = build_semantic_index
    edge_indexes: EdgeIndexBuilder = build_edge_id_indexes
