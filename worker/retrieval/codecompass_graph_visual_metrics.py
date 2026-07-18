"""Materialize deterministic visual metrics for a CodeCompass graph revision.

This module runs in the worker/indexing boundary.  It intentionally exposes a
small store port and produces a versioned artifact; Hub request handlers only
read and project that artifact and never import these algorithms.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable, Mapping
from typing import Any, Protocol

VISUAL_METRICS_SCHEMA = "graph_visual_metrics.v1"
VISUAL_METRICS_ALGORITHM_VERSION = "codecompass_graph_visual_metrics.v1"
ADVANCED_METRICS_NODE_CAP = 250
BLAST_RADIUS_SEED_CAP = 25

_PARENT_TO_CHILD_RELATIONS = frozenset({
    "contains_entry",
    "contains_method",
    "contains_section",
    "contains_symbol",
    "contains_type",
    "declares_bean",
    "declares_constructor",
    "declares_method",
    "parent_child",
})
_CHILD_TO_PARENT_RELATIONS = frozenset({"child_of_file", "child_of_type"})


class GraphVisualMetricsStore(Protocol):
    """Minimal persistence port required by the worker materializer."""

    def load(self) -> dict[str, Any]: ...

    def publish_visual_metrics(self, artifact: dict[str, Any]) -> None: ...


def _canonical_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _revision_node_attributes(node: Mapping[str, Any]) -> dict[str, Any]:
    candidates: list[Mapping[str, Any]] = [node]
    for field in ("attributes", "attrs", "source_record"):
        value = node.get(field)
        if isinstance(value, Mapping):
            candidates.append(value)
    projected: dict[str, Any] = {}
    for key in (
        "file",
        "domain_id",
        "domain_path",
        "domain_level",
        "importance_score",
        "code_extent",
        "line_count",
        "lines_of_code",
        "usage_frequency",
        "usage_count",
        "reference_count",
    ):
        for candidate in candidates:
            if key in candidate and candidate[key] is not None:
                projected[key] = candidate[key]
                break
    for candidate in candidates:
        metrics = candidate.get("metrics")
        if isinstance(metrics, Mapping):
            projected["metrics"] = dict(sorted(metrics.items()))
            break
    return projected


def _edge_value(edge: Mapping[str, Any], key: str) -> Any:
    """Read one edge value without truthiness fallbacks.

    Edge records exist in several backward-compatible shapes. Keeping this
    lookup in one place makes the worker revision and the Hub projection cover
    the same style-relevant evidence, including explicit zero values.
    """
    if key in edge and edge[key] is not None:
        return edge[key]
    attributes = edge.get("attributes")
    if isinstance(attributes, Mapping) and key in attributes and attributes[key] is not None:
        return attributes[key]
    return None


def _revision_edge_attributes(edge: Mapping[str, Any]) -> dict[str, Any]:
    projected: dict[str, Any] = {}
    for key in ("confidence", "multiplicity", "dependency_weight", "directed"):
        value = _edge_value(edge, key)
        if value is not None:
            projected[key] = value
    metrics: dict[str, Any] = {}
    attributes = edge.get("attributes")
    if isinstance(attributes, Mapping):
        raw_metrics = attributes.get("metrics") or attributes.get("visual_metrics")
        if isinstance(raw_metrics, Mapping):
            metrics.update(raw_metrics)
    raw_metrics = edge.get("metrics")
    if isinstance(raw_metrics, Mapping):
        metrics.update(raw_metrics)
    if metrics:
        projected["metrics"] = dict(sorted(metrics.items()))
    return projected


def graph_revision_from_payload(payload: Mapping[str, Any]) -> str:
    """Return the existing manifest revision or a deterministic graph digest."""
    state = payload.get("state")
    if isinstance(state, Mapping):
        manifest_hash = str(state.get("manifest_hash") or "").strip()
        if manifest_hash:
            return manifest_hash

    nodes = []
    for item in [*(payload.get("nodes") or []), *(payload.get("semantic_nodes") or [])]:
        if not isinstance(item, Mapping):
            continue
        nodes.append({
            "node_id": str(item.get("id") or item.get("node_id") or ""),
            "raw_node_type": str(
                item.get("raw_node_type")
                or item.get("kind")
                or item.get("node_type")
                or "unknown"
            ),
            "visual_attributes": _revision_node_attributes(item),
        })
    edges = []
    for item in [*(payload.get("edges") or []), *(payload.get("semantic_edges") or [])]:
        if not isinstance(item, Mapping):
            continue
        edges.append({
            "edge_id": str(item.get("edge_id") or ""),
            "source_id": str(item.get("source_id") or item.get("source") or ""),
            "target_id": str(item.get("target_id") or item.get("target") or ""),
            "raw_edge_type": str(
                item.get("raw_edge_type")
                or item.get("edge_type")
                or item.get("relation")
                or item.get("type")
                or "related"
            ),
            "visual_attributes": _revision_edge_attributes(item),
        })
    revision_input = {
        "nodes": sorted(
            nodes,
            key=lambda row: (
                row["node_id"],
                row["raw_node_type"],
                _canonical_json(row["visual_attributes"]),
            ),
        ),
        "edges": sorted(
            edges,
            key=lambda row: (
                row["source_id"],
                row["target_id"],
                row["raw_edge_type"],
                row["edge_id"],
                _canonical_json(row["visual_attributes"]),
            ),
        ),
    }
    digest = hashlib.sha256(_canonical_json(revision_input).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _capability(
    status: str,
    *,
    source: str,
    algorithm_version: str,
    scope: str = "all_nodes",
    reason_code: str | None = None,
    limits: Mapping[str, str | int | float | bool | None] | None = None,
    entity: str = "node",
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "status": status,
        "source": source,
        "algorithm_version": algorithm_version,
        "scope": scope,
        "entity": entity,
    }
    if reason_code:
        result["reason_code"] = reason_code
    if limits:
        result["limits"] = dict(sorted(limits.items()))
    return result


def _finite_non_negative(value: Any, *, metric_name: str, node_id: str) -> float | int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"invalid_metric_value:{metric_name}:{node_id}")
    numeric = float(value)
    maximum = 1.0 if metric_name in {"blast_radius", "bridge_score"} else None
    if not math.isfinite(numeric) or numeric < 0 or (maximum is not None and numeric > maximum):
        raise ValueError(f"invalid_metric_value:{metric_name}:{node_id}")
    return value


def _metric_evidence(node: Mapping[str, Any], *keys: str) -> Any:
    candidates: list[Mapping[str, Any]] = [node]
    for field in ("attributes", "attrs", "source_record"):
        value = node.get(field)
        if isinstance(value, Mapping):
            candidates.append(value)
    for candidate in candidates:
        for key in keys:
            if key in candidate and candidate[key] is not None:
                return candidate[key]
    return None


def _base_metric_rows(payload: Mapping[str, Any]) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    nodes_by_id: dict[str, Mapping[str, Any]] = {}
    for node in [*(payload.get("nodes") or []), *(payload.get("semantic_nodes") or [])]:
        if not isinstance(node, Mapping):
            continue
        node_id = str(node.get("id") or node.get("node_id") or "").strip()
        if node_id:
            nodes_by_id[node_id] = node

    values_by_id: dict[str, dict[str, Any]] = {
        node_id: {
            "direct_containment_children": 0,
            "in_degree": 0,
            "out_degree": 0,
            "total_degree": 0,
        }
        for node_id in nodes_by_id
    }
    containment_children: dict[str, set[str]] = {node_id: set() for node_id in nodes_by_id}

    for edge in [*(payload.get("edges") or []), *(payload.get("semantic_edges") or [])]:
        if not isinstance(edge, Mapping):
            continue
        source_id = str(edge.get("source_id") or edge.get("source") or "").strip()
        target_id = str(edge.get("target_id") or edge.get("target") or "").strip()
        relation = str(
            edge.get("edge_type")
            or edge.get("relation")
            or edge.get("type")
            or "related"
        ).strip().lower()
        if source_id in values_by_id:
            values_by_id[source_id]["out_degree"] += 1
            values_by_id[source_id]["total_degree"] += 1
        if target_id in values_by_id:
            values_by_id[target_id]["in_degree"] += 1
            values_by_id[target_id]["total_degree"] += 1
        if relation in _PARENT_TO_CHILD_RELATIONS and source_id in containment_children and target_id in nodes_by_id:
            containment_children[source_id].add(target_id)
        elif relation in _CHILD_TO_PARENT_RELATIONS and target_id in containment_children and source_id in nodes_by_id:
            containment_children[target_id].add(source_id)

    code_extent_nodes = 0
    usage_nodes = 0
    for node_id, node in nodes_by_id.items():
        values = values_by_id[node_id]
        values["direct_containment_children"] = len(containment_children[node_id])
        code_extent = _metric_evidence(node, "code_extent", "line_count", "lines_of_code")
        if code_extent is not None:
            values["code_extent"] = _finite_non_negative(
                code_extent,
                metric_name="code_extent",
                node_id=node_id,
            )
            code_extent_nodes += 1
        usage = _metric_evidence(node, "usage_frequency", "usage_count", "reference_count")
        if usage is not None:
            values["usage_frequency"] = _finite_non_negative(
                usage,
                metric_name="usage_frequency",
                node_id=node_id,
            )
            usage_nodes += 1

    node_count = len(nodes_by_id)
    capabilities = {
        "code_extent": _evidence_capability(
            code_extent_nodes,
            node_count,
            source="codecompass_graph_node_evidence",
            missing_reason="code_extent_evidence_missing",
        ),
        "descendant_count": _capability(
            "unavailable",
            source="codecompass_graph_index",
            algorithm_version=VISUAL_METRICS_ALGORITHM_VERSION,
            reason_code="descendant_materialization_not_requested",
        ),
        "direct_containment_children": _capability(
            "available" if node_count else "not_applicable",
            source="codecompass_graph_index",
            algorithm_version=VISUAL_METRICS_ALGORITHM_VERSION,
            reason_code=None if node_count else "empty_graph",
        ),
        "in_degree": _capability(
            "available" if node_count else "not_applicable",
            source="codecompass_graph_index",
            algorithm_version=VISUAL_METRICS_ALGORITHM_VERSION,
            reason_code=None if node_count else "empty_graph",
        ),
        "out_degree": _capability(
            "available" if node_count else "not_applicable",
            source="codecompass_graph_index",
            algorithm_version=VISUAL_METRICS_ALGORITHM_VERSION,
            reason_code=None if node_count else "empty_graph",
        ),
        "total_degree": _capability(
            "available" if node_count else "not_applicable",
            source="codecompass_graph_index",
            algorithm_version=VISUAL_METRICS_ALGORITHM_VERSION,
            reason_code=None if node_count else "empty_graph",
        ),
        "usage_frequency": _evidence_capability(
            usage_nodes,
            node_count,
            source="codecompass_graph_node_evidence",
            missing_reason="usage_frequency_evidence_missing",
        ),
    }
    return [
        {"node_id": node_id, "values": dict(sorted(values_by_id[node_id].items()))}
        for node_id in sorted(values_by_id)
    ], capabilities


def _evidence_capability(
    evidence_count: int,
    node_count: int,
    *,
    source: str,
    missing_reason: str,
) -> dict[str, Any]:
    if not node_count:
        return _capability(
            "not_applicable",
            source=source,
            algorithm_version=VISUAL_METRICS_ALGORITHM_VERSION,
            reason_code="empty_graph",
        )
    if evidence_count == node_count:
        return _capability(
            "available",
            source=source,
            algorithm_version=VISUAL_METRICS_ALGORITHM_VERSION,
        )
    if evidence_count:
        return _capability(
            "approximate",
            source=source,
            algorithm_version=VISUAL_METRICS_ALGORITHM_VERSION,
            scope="subset",
            reason_code="partial_node_evidence",
            limits={"evidence_node_count": evidence_count, "graph_node_count": node_count},
        )
    return _capability(
        "unavailable",
        source=source,
        algorithm_version=VISUAL_METRICS_ALGORITHM_VERSION,
        reason_code=missing_reason,
    )


def _content_hash(artifact_without_hash: Mapping[str, Any]) -> str:
    digest = hashlib.sha256(_canonical_json(artifact_without_hash).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def verify_visual_metrics_content_hash(artifact: Mapping[str, Any]) -> bool:
    expected = str(artifact.get("content_hash") or "")
    unsigned = {key: value for key, value in artifact.items() if key != "content_hash"}
    return bool(expected) and expected == _content_hash(unsigned)


def build_graph_visual_metrics(
    *,
    graph_payload: Mapping[str, Any],
    advanced_metrics: Mapping[str, Mapping[str, float | int]] | None = None,
    advanced_capabilities: Mapping[str, Mapping[str, Any]] | None = None,
    advanced_metrics_requested: bool = False,
    warnings: Iterable[str] = (),
) -> dict[str, Any]:
    """Build and hash an artifact from graph topology and optional evidence.

    ``advanced_metrics`` is an explicit worker-side evidence seam.  It keeps the
    pure builder testable and prevents the Hub from needing metric algorithms.
    """
    rows, capabilities = _base_metric_rows(graph_payload)
    row_index = {row["node_id"]: row["values"] for row in rows}
    for metric_name, values_by_node in sorted((advanced_metrics or {}).items()):
        for node_id, value in sorted(values_by_node.items()):
            if node_id not in row_index:
                continue
            row_index[node_id][metric_name] = _finite_non_negative(
                value,
                metric_name=metric_name,
                node_id=node_id,
            )
    for name, capability in sorted((advanced_capabilities or {}).items()):
        capabilities[name] = {"entity": "node", **dict(capability)}

    for name, reason in (
        ("blast_radius", "seed_scope_not_provided"),
        ("bridge_score", "advanced_metrics_not_requested"),
        ("degree_centrality", "advanced_metrics_not_requested"),
    ):
        capabilities.setdefault(
            name,
            _capability(
                "unavailable",
                source="codecompass_graph_metrics",
                algorithm_version=VISUAL_METRICS_ALGORITHM_VERSION,
                reason_code=reason,
            ),
        )

    for row in rows:
        row["values"] = dict(sorted(row["values"].items()))
    sorted_capabilities = dict(sorted(capabilities.items()))
    statuses = {str(capability.get("status") or "unavailable") for capability in sorted_capabilities.values()}
    if statuses <= {"available", "not_applicable"}:
        capability_status = "available"
    elif "available" in statuses or "approximate" in statuses:
        capability_status = "degraded"
    else:
        capability_status = "unavailable"

    edges = [
        item
        for item in [
            *(graph_payload.get("edges") or []),
            *(graph_payload.get("semantic_edges") or []),
        ]
        if isinstance(item, Mapping)
    ]
    artifact: dict[str, Any] = {
        "schema": VISUAL_METRICS_SCHEMA,
        "graph_revision": graph_revision_from_payload(graph_payload),
        "algorithm_version": VISUAL_METRICS_ALGORITHM_VERSION,
        "capability_status": capability_status,
        "metric_capabilities": sorted_capabilities,
        "nodes": rows,
        "metadata": {
            "node_count": len(rows),
            "edge_count": len(edges),
            "advanced_metrics_requested": bool(advanced_metrics_requested),
            "limits": {
                "advanced_metrics_node_cap": ADVANCED_METRICS_NODE_CAP,
                "blast_radius_seed_cap": BLAST_RADIUS_SEED_CAP,
            },
            "warnings": sorted({str(item) for item in warnings if str(item)}),
        },
    }
    artifact["content_hash"] = _content_hash(artifact)
    return artifact


def materialize_graph_visual_metrics(
    *,
    graph_store: GraphVisualMetricsStore,
    include_advanced_metrics: bool = False,
    blast_radius_seeds: Iterable[str] = (),
) -> dict[str, Any]:
    """Compute worker-side evidence and atomically publish one metrics artifact."""
    payload = graph_store.load()
    nodes = [
        item
        for item in [
            *(payload.get("nodes") or []),
            *(payload.get("semantic_nodes") or []),
        ]
        if isinstance(item, Mapping)
    ]
    node_ids = sorted({str(item.get("id") or item.get("node_id") or "").strip() for item in nodes} - {""})
    advanced_values: dict[str, dict[str, float | int]] = {}
    advanced_capabilities: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []

    if include_advanced_metrics and node_ids and len(node_ids) <= ADVANCED_METRICS_NODE_CAP:
        from worker.retrieval.codecompass_graph_metrics import (
            BETWEENNESS_DEPTH_CAP,
            BETWEENNESS_PATH_CAP,
            METRICS_SCHEMA_VERSION,
            compute_graph_metrics,
        )

        result = compute_graph_metrics(graph_store=graph_store, top_k=len(node_ids))
        hub_scores = {item.node_id: item.score for item in result.hub_nodes}
        bridge_scores = {item.node_id: item.score for item in result.bridge_nodes}
        advanced_values["degree_centrality"] = {node_id: hub_scores.get(node_id, 0.0) for node_id in node_ids}
        advanced_values["bridge_score"] = {node_id: bridge_scores.get(node_id, 0.0) for node_id in node_ids}
        advanced_capabilities["degree_centrality"] = _capability(
            "available",
            source="codecompass_graph_metrics",
            algorithm_version=METRICS_SCHEMA_VERSION,
        )
        advanced_capabilities["bridge_score"] = _capability(
            "approximate",
            source="codecompass_graph_metrics",
            algorithm_version=METRICS_SCHEMA_VERSION,
            reason_code="bounded_shortest_path_approximation",
            limits={
                "depth_cap": BETWEENNESS_DEPTH_CAP,
                "path_cap": BETWEENNESS_PATH_CAP,
            },
        )
        warnings.extend(result.warnings)
    elif include_advanced_metrics and len(node_ids) > ADVANCED_METRICS_NODE_CAP:
        for metric_name in ("degree_centrality", "bridge_score"):
            advanced_capabilities[metric_name] = _capability(
                "unavailable",
                source="codecompass_graph_metrics",
                algorithm_version=VISUAL_METRICS_ALGORITHM_VERSION,
                reason_code="advanced_metrics_node_cap_reached",
                limits={"node_cap": ADVANCED_METRICS_NODE_CAP, "graph_node_count": len(node_ids)},
            )
        warnings.append("advanced_metrics_node_cap_reached")
    elif include_advanced_metrics:
        for metric_name in ("degree_centrality", "bridge_score"):
            advanced_capabilities[metric_name] = _capability(
                "not_applicable",
                source="codecompass_graph_metrics",
                algorithm_version=VISUAL_METRICS_ALGORITHM_VERSION,
                reason_code="empty_graph",
            )

    requested_seeds = sorted({str(item).strip() for item in blast_radius_seeds if str(item).strip()})
    valid_seeds = [item for item in requested_seeds if item in node_ids][:BLAST_RADIUS_SEED_CAP]
    if valid_seeds:
        from worker.retrieval.codecompass_blast_radius import RISK_MODEL_VERSION, compute_blast_radius

        scores: dict[str, float] = {}
        for seed in valid_seeds:
            result = compute_blast_radius(graph_store=graph_store, seed_nodes=(seed,))
            scores[seed] = result.risk_score
            warnings.extend(result.warnings)
        advanced_values["blast_radius"] = scores
        advanced_capabilities["blast_radius"] = _capability(
            "approximate",
            source="codecompass_blast_radius",
            algorithm_version=RISK_MODEL_VERSION,
            scope="subset",
            reason_code="seed_scoped_metric",
            limits={"seed_count": len(valid_seeds), "seed_cap": BLAST_RADIUS_SEED_CAP},
        )
        if len(requested_seeds) > BLAST_RADIUS_SEED_CAP:
            warnings.append("blast_radius_seed_cap_reached")
    elif requested_seeds:
        advanced_capabilities["blast_radius"] = _capability(
            "unavailable",
            source="codecompass_blast_radius",
            algorithm_version=VISUAL_METRICS_ALGORITHM_VERSION,
            reason_code="seed_nodes_not_found",
        )

    artifact = build_graph_visual_metrics(
        graph_payload=payload,
        advanced_metrics=advanced_values,
        advanced_capabilities=advanced_capabilities,
        advanced_metrics_requested=include_advanced_metrics,
        warnings=warnings,
    )
    graph_store.publish_visual_metrics(artifact)
    return artifact


__all__ = [
    "ADVANCED_METRICS_NODE_CAP",
    "BLAST_RADIUS_SEED_CAP",
    "GraphVisualMetricsStore",
    "VISUAL_METRICS_ALGORITHM_VERSION",
    "VISUAL_METRICS_SCHEMA",
    "build_graph_visual_metrics",
    "graph_revision_from_payload",
    "materialize_graph_visual_metrics",
    "verify_visual_metrics_content_hash",
]
