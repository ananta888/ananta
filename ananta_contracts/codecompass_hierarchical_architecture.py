"""Deterministic, container-neutral CodeCompass hierarchy projection."""

from __future__ import annotations

from typing import Any, Mapping

LEVELS = ("system", "subsystem", "component", "file", "symbol")

_RELATION_MAP = {
    "contains": "contains",
    "parent_child": "contains",
    "child_of_type": "contains",
    "uses": "uses",
    "calls": "calls",
    "calls_probable_target": "calls",
    "depends_on": "depends_on",
    "injects_dependency": "depends_on",
    "constructor_injection": "depends_on",
    "service_uses_repository": "depends_on",
    "implements": "implements",
    "exposes_tool": "exposes_tool",
    "stores": "stores",
    "retrieves_from": "retrieves_from",
    "governed_by": "governed_by",
    "provides_context_to": "provides_context_to",
    # Canonical persisted graph vocabulary.
    "contains_file": "contains",
    "contains_directory": "contains",
    "declares": "contains",
}

_INFERRED_RELATIONS = frozenset({"calls_probable_target"})
_SYSTEM_MARKERS = ("system", "platform", "product")
_SUBSYSTEM_MARKERS = ("subsystem", "domain", "bounded_context", "module")
_COMPONENT_MARKERS = ("component", "service", "package", "worker", "adapter")
_FILE_MARKERS = ("file", "document", "config")
_SYMBOL_MARKERS = (
    "symbol",
    "function",
    "method",
    "class",
    "type",
    "interface",
    "enum",
)


def classify_level(record: Mapping[str, Any]) -> str:
    kind = str(record.get("kind") or record.get("record_kind") or "").lower()
    level = str(record.get("level") or "").lower()
    if level in LEVELS:
        return level
    path = str(record.get("path") or record.get("file") or "")
    tokens = {
        part
        for part in kind.replace("-", "_").replace(".", "_").split("_")
        if part
    }
    if kind == "repository":
        return "system"
    if kind == "directory":
        depth = len([part for part in path.replace("\\", "/").split("/") if part])
        return "subsystem" if depth <= 1 else "component"
    if kind == "source_file":
        return "file"
    if kind.startswith(("python_", "java_", "ts_")) or tokens & {
        "function",
        "class",
        "method",
        "symbol",
        "type",
    }:
        return "symbol"
    if tokens & set(_SYSTEM_MARKERS) and "subsystem" not in tokens:
        return "system"
    if tokens & set(_SUBSYSTEM_MARKERS) or "subsystem" in tokens:
        return "subsystem"
    if tokens & set(_COMPONENT_MARKERS):
        return "component"
    if path and "." in path.split("/")[-1] and not (tokens & set(_SYMBOL_MARKERS)):
        return "file"
    if tokens & set(_FILE_MARKERS):
        return "file"
    if tokens & set(_SYMBOL_MARKERS):
        return "symbol"
    return "unknown"


def map_relation(raw: str) -> tuple[str | None, str]:
    token = str(raw or "").strip().lower()
    mapped = _RELATION_MAP.get(token)
    if mapped is None:
        return None, "inferred"
    origin = "inferred" if token in _INFERRED_RELATIONS else "deterministic"
    return mapped, origin


def project_hierarchy(
    *,
    records: list[Mapping[str, Any]] | None = None,
    edges: list[Mapping[str, Any]] | None = None,
    revision: str = "",
) -> dict[str, Any]:
    nodes: list[dict[str, Any]] = []
    for record in list(records or []):
        if not isinstance(record, Mapping):
            continue
        node_id = str(record.get("id") or record.get("node_id") or "").strip()
        title = str(
            record.get("title")
            or record.get("name")
            or record.get("path")
            or node_id
        ).strip()
        if not node_id or not title:
            continue
        path = str(record.get("path") or record.get("file") or "")
        source_ref = path or node_id
        level = classify_level(record)
        nodes.append(
            {
                "id": node_id,
                "level": level,
                "title": title,
                "short_summary": str(
                    record.get("summary") or record.get("content") or ""
                )[:240],
                "responsibilities": [
                    item
                    for item in list(record.get("responsibilities") or [])
                    if str(item).strip()
                ],
                "source_refs": [source_ref],
                "verification_status": str(
                    record.get("verification_status") or "unverified"
                ),
                "trust_level": str(record.get("trust_level") or "extracted"),
                "confidence": float(record.get("confidence") or 0.0),
                "expandable": level not in {"symbol", "unknown"},
                "parent_id": str(record.get("parent_id") or ""),
                "path": path,
            }
        )
    nodes.sort(
        key=lambda item: (
            LEVELS.index(item["level"]) if item["level"] in LEVELS else 9,
            item["id"],
        )
    )
    projected_edges: list[dict[str, Any]] = []
    known = {item["id"] for item in nodes}
    for edge in list(edges or []):
        if not isinstance(edge, Mapping):
            continue
        source = str(
            edge.get("source") or edge.get("source_id") or edge.get("from") or ""
        ).strip()
        target = str(
            edge.get("target") or edge.get("target_id") or edge.get("to") or ""
        ).strip()
        relation, origin = map_relation(
            str(
                edge.get("type")
                or edge.get("edge_type")
                or edge.get("relation")
                or ""
            )
        )
        if not source or not target or relation is None:
            continue
        if source not in known or target not in known:
            continue
        projected_edges.append(
            {
                "id": str(
                    edge.get("id")
                    or edge.get("edge_id")
                    or f"e-{source}-{target}-{relation}"
                ),
                "source": source,
                "target": target,
                "relation": relation,
                "origin": origin,
            }
        )
    projected_edges.sort(
        key=lambda item: (
            item["source"],
            item["target"],
            item["relation"],
            item["id"],
        )
    )
    return {
        "revision": str(revision or ""),
        "nodes": nodes,
        "edges": projected_edges,
        "unknown_count": sum(1 for item in nodes if item["level"] == "unknown"),
    }
