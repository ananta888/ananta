"""Assemble a CodeCompass graph payload from reader output records.

The assembler owns the normalization of every supported output kind and the
diagnostics derived from them. Persistence and post-save side effects stay
with the graph store that invokes it.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ananta_codecompass.graph_edge_identity import (
    finite_non_negative,
    stable_edge_id,
    validate_edge_metric_values,
)
from ananta_codecompass.graph_indexes import GraphIndexBuilders


def normalize_semantic_node(record: dict[str, Any], manifest_hash: str, index: int) -> dict[str, Any]:
    provenance = dict(record.get("provenance") or {})
    node_id = str(record.get("id") or record.get("node_id") or f"semantic-node:{index}").strip()
    raw_node_type = str(record.get("kind") or "semantic_node") or "semantic_node"
    return {
        "id": node_id,
        "file": str(provenance.get("file") or record.get("file") or record.get("path") or "").strip(),
        "kind": raw_node_type.strip().lower(),
        "raw_node_type": raw_node_type,
        "semantic_kind": str(record.get("semantic_kind") or "").strip().lower(),
        "language": str(record.get("language") or provenance.get("language") or "").strip().lower(),
        "symbol": str(record.get("symbol") or provenance.get("symbol") or record.get("name") or "").strip(),
        "rule_id": str(record.get("rule_id") or "").strip(),
        "record_id": str(record.get("record_id") or node_id).strip(),
        "attributes": dict(record.get("attributes") or {}),
        "provenance": {**provenance, "manifest_hash": str(manifest_hash or "")},
        "source_record": dict(record),
    }


def normalize_semantic_edge(
    record: dict[str, Any],
    manifest_hash: str,
    edge_identity_occurrences: dict[str, int],
) -> dict[str, Any] | None:
    source_id = str(record.get("source") or record.get("source_id") or "").strip()
    target_id = str(record.get("target") or record.get("target_id") or "").strip()
    if not source_id or not target_id:
        return None
    raw_edge_type = str(record.get("edge_type") or record.get("type") or "related") or "related"
    edge_type = raw_edge_type.strip().lower()
    confidence_value = record.get("confidence")
    if confidence_value is None:
        confidence_value = (record.get("provenance") or {}).get("confidence")
    if confidence_value is None:
        confidence_value = 1.0
    confidence_value = finite_non_negative(
        confidence_value,
        field="confidence",
        maximum=1,
    )
    validate_edge_metric_values(record)
    edge: dict[str, Any] = {
        "edge_id": stable_edge_id(
            record,
            source_id=source_id,
            target_id=target_id,
            raw_edge_type=raw_edge_type,
            occurrences=edge_identity_occurrences,
        ),
        "source_id": source_id,
        "target_id": target_id,
        "edge_type": edge_type,
        "raw_edge_type": raw_edge_type,
        "rule_id": str(record.get("rule_id") or "").strip(),
        "confidence": float(confidence_value),
        "attributes": dict(record.get("attributes") or {}),
        "provenance": {
            **dict(record.get("provenance") or {}),
            "manifest_hash": str(manifest_hash or ""),
            "output_kind": "semantic_edges",
        },
        "source_record": dict(record),
    }
    for field in ("multiplicity", "dependency_weight", "directed", "metrics"):
        value = record.get(field)
        if value is not None:
            edge[field] = dict(value) if field == "metrics" and isinstance(value, dict) else value
    return edge


def _normalize_graph_node(record: dict[str, Any], index: int) -> dict[str, Any]:
    node_id = str(record.get("id") or record.get("node_id") or f"node:{index}").strip()
    raw_node_type = str(record.get("kind") or record.get("type") or "unknown") or "unknown"
    return {
        "id": node_id,
        "file": str(record.get("file") or record.get("path") or "").strip(),
        "kind": raw_node_type.strip().lower(),
        "raw_node_type": raw_node_type,
        "name": str(record.get("name") or record.get("symbol") or "").strip(),
        "record_id": str(record.get("record_id") or node_id).strip(),
        "content": str(record.get("content") or record.get("summary") or "").strip(),
        "source_record": record,
    }


def _normalize_graph_edge(
    record: dict[str, Any],
    *,
    manifest_hash: str,
    output_kind: str,
    edge_identity_occurrences: dict[str, int],
) -> dict[str, Any] | None:
    source_id = str(record.get("source") or record.get("source_id") or "").strip()
    target_id = str(record.get("target") or record.get("target_id") or "").strip()
    if not source_id or not target_id:
        return None
    raw_edge_type = str(record.get("type") or record.get("edge_type") or "related") or "related"
    edge_type = raw_edge_type.strip().lower()
    confidence_value = record.get("confidence")
    if confidence_value is None:
        confidence_value = 1.0
    confidence_value = finite_non_negative(
        confidence_value,
        field="confidence",
        maximum=1,
    )
    validate_edge_metric_values(record)
    edge_id = stable_edge_id(
        record,
        source_id=source_id,
        target_id=target_id,
        raw_edge_type=raw_edge_type,
        occurrences=edge_identity_occurrences,
    )
    edge: dict[str, Any] = {
        "edge_id": edge_id,
        "source_id": source_id,
        "target_id": target_id,
        "edge_type": edge_type,
        "raw_edge_type": raw_edge_type,
        "confidence": float(confidence_value),
        "provenance": {
            "manifest_hash": str(manifest_hash or ""),
            "output_kind": output_kind,
        },
    }
    if record.get("multiplicity") is not None:
        edge["multiplicity"] = record["multiplicity"]
    if record.get("dependency_weight") is not None:
        edge["dependency_weight"] = record["dependency_weight"]
    if record.get("directed") is not None:
        edge["directed"] = bool(record["directed"])
    if isinstance(record.get("metrics"), dict):
        edge["metrics"] = dict(record["metrics"])
    for attribute_key in ("field", "operation", "heuristic"):
        if record.get(attribute_key) is not None:
            edge[attribute_key] = record[attribute_key]
    return edge


class GraphOutputRecordAssembler:
    """Turn CodeCompass output records into a graph payload plus diagnostics."""

    def assemble(  # noqa: C901 - compatibility dispatcher for existing output kinds
        self,
        *,
        records: list[dict[str, Any]],
        manifest_hash: str,
        semantic_budget: Mapping[str, Any] | None,
        index_builders: GraphIndexBuilders,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        nodes: list[dict[str, Any]] = []
        edges: list[dict[str, Any]] = []
        semantic_nodes: list[dict[str, Any]] = []
        semantic_edges: list[dict[str, Any]] = []
        equivalence_rules: list[dict[str, Any]] = []
        translation_contracts: list[dict[str, Any]] = []
        transform_artifacts: list[dict[str, Any]] = []
        # X86CC-020: separate storage so existing graph_nodes / graph_edges indexes
        # are not polluted. x86 records keep their full record shape for round-trip
        # queries via X86QueryEngine.
        x86_nodes_list: list[dict[str, Any]] = []
        x86_edges_list: list[dict[str, Any]] = []
        # RIG-002: rig_nodes / rig_edges slots; never pollutes the symbolgraph.
        rig_nodes_list: list[dict[str, Any]] = []
        rig_edges_list: list[dict[str, Any]] = []
        has_nodes = False
        has_edges = False
        edge_identity_occurrences: dict[str, int] = {}
        for index, record in enumerate(list(records or []), start=1):
            if not isinstance(record, dict):
                continue
            provenance = dict(record.get("_provenance") or {})
            output_kind = str(provenance.get("output_kind") or "").strip().lower()
            if output_kind == "graph_nodes":
                has_nodes = True
                nodes.append(_normalize_graph_node(record, index))
            elif output_kind == "graph_edges":
                has_edges = True
                edge = _normalize_graph_edge(
                    record,
                    manifest_hash=manifest_hash,
                    output_kind=output_kind,
                    edge_identity_occurrences=edge_identity_occurrences,
                )
                if edge is not None:
                    edges.append(edge)
            elif output_kind == "semantic_nodes":
                semantic_nodes.append(normalize_semantic_node(record, manifest_hash, index))
            elif output_kind == "semantic_edges":
                edge = normalize_semantic_edge(
                    record,
                    manifest_hash,
                    edge_identity_occurrences,
                )
                if edge:
                    semantic_edges.append(edge)
            elif output_kind == "equivalence_rules":
                equivalence_rules.append(dict(record))
            elif output_kind == "translation_contracts":
                translation_contracts.append(dict(record))
            elif output_kind == "transform_artifacts":
                transform_artifacts.append(dict(record))
            elif output_kind == "x86_nodes":
                # X86CC-020: x86 nodes are stored as a separate list under x86_nodes
                # so the existing graph_nodes index is not polluted. They participate
                # in their own (nodes_by_id-like) lookup but the general graph_nodes
                # index stays unchanged for backward compatibility.
                x86_nodes_list.append(dict(record))
            elif output_kind == "x86_edges":
                x86_edges_list.append(dict(record))
            elif output_kind == "rig_nodes":
                # RIG-002: rig_nodes lives under its own slot; never enters the
                # symbolgraph nodes/edges index. Records keep their full shape.
                rig_nodes_list.append(dict(record))
            elif output_kind == "rig_edges":
                rig_edges_list.append(dict(record))

        node_index = index_builders.node_index(nodes)
        semantic_index = index_builders.semantic_index(semantic_nodes, semantic_edges, equivalence_rules)
        outgoing_index, incoming_index = index_builders.edge_indexes([*edges, *semantic_edges])
        diagnostics = {"status": "ready", "reason": "graph_loaded", "node_count": len(nodes), "edge_count": len(edges)}
        if not has_nodes or not has_edges:
            diagnostics = {
                "status": "degraded",
                "reason": "missing_graph_outputs",
                "node_count": len(nodes),
                "edge_count": len(edges),
            }
        diagnostics["semantic_translation"] = _semantic_translation_diagnostics(
            semantic_nodes=semantic_nodes,
            semantic_edges=semantic_edges,
            equivalence_rules=equivalence_rules,
            translation_contracts=translation_contracts,
            transform_artifacts=transform_artifacts,
            semantic_budget=semantic_budget,
        )
        x86_index, diagnostics["x86_extension"] = _x86_extension(x86_nodes_list, x86_edges_list)
        rig_index, diagnostics["repository_intelligence"] = _repository_intelligence(
            rig_nodes_list,
            rig_edges_list,
        )

        payload = {
            "state": {
                "schema": "codecompass_graph_index.v1",
                "manifest_hash": str(manifest_hash or ""),
                "edge_index_encoding": "edge_id",
            },
            "nodes": nodes,
            "edges": edges,
            "semantic_nodes": semantic_nodes,
            "semantic_edges": semantic_edges,
            "equivalence_rules": equivalence_rules,
            "translation_contracts": translation_contracts,
            "transform_artifacts": transform_artifacts,
            "x86_nodes": x86_nodes_list,
            "x86_edges": x86_edges_list,
            "x86_index": x86_index,
            "rig_nodes": rig_nodes_list,
            "rig_edges": rig_edges_list,
            "rig_index": rig_index,
            "node_index": node_index,
            "semantic_index": semantic_index,
            "outgoing_index": outgoing_index,
            "incoming_index": incoming_index,
            "diagnostics": diagnostics,
        }
        return payload, diagnostics


def _semantic_translation_diagnostics(
    *,
    semantic_nodes: list[dict[str, Any]],
    semantic_edges: list[dict[str, Any]],
    equivalence_rules: list[dict[str, Any]],
    translation_contracts: list[dict[str, Any]],
    transform_artifacts: list[dict[str, Any]],
    semantic_budget: Mapping[str, Any] | None,
) -> dict[str, Any]:
    normalized_semantic_budget = dict(semantic_budget or {})
    if semantic_nodes or semantic_edges or equivalence_rules or transform_artifacts:
        semantic_translation: dict[str, Any] = {
            "schema": "codecompass_semantic_translation_graph.v1",
            "semantic_node_count": len(semantic_nodes),
            "semantic_edge_count": len(semantic_edges),
            "equivalence_rule_count": len(equivalence_rules),
            "translation_contract_count": len(translation_contracts),
            "transform_artifact_count": len(transform_artifacts),
            "status": "ready",
        }
    else:
        semantic_translation = {
            "status": "degraded",
            "reason": "semantic_translation_index_unavailable",
        }
    if normalized_semantic_budget:
        semantic_translation["semantic_budget"] = normalized_semantic_budget
        if (
            bool(normalized_semantic_budget.get("truncated"))
            or int(normalized_semantic_budget.get("unresolved_edge_count") or 0)
        ):
            semantic_translation["status"] = "degraded"
            semantic_translation["reason"] = "semantic_graph_partial"
    return semantic_translation


def _x86_extension(
    x86_nodes_list: list[dict[str, Any]],
    x86_edges_list: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    if x86_nodes_list or x86_edges_list:
        # X86CC-020: expose x86 records as their own structure with a deterministic
        # id-keyed lookup so X86QueryEngine can run against the same payload
        # without rebuilding the index.
        x86_index = {
            "schema": "codecompass_x86_graph.v1",
            "nodes": x86_nodes_list,
            "edges": x86_edges_list,
            "nodes_by_id": {n["id"]: n for n in x86_nodes_list if isinstance(n, dict) and n.get("id")},
            "node_count": len(x86_nodes_list),
            "edge_count": len(x86_edges_list),
        }
        return x86_index, {
            "schema": "codecompass_x86_graph.v1",
            "node_count": len(x86_nodes_list),
            "edge_count": len(x86_edges_list),
            "status": "ready",
        }
    x86_index = {
        "schema": "codecompass_x86_graph.v1",
        "nodes": [],
        "edges": [],
        "nodes_by_id": {},
        "node_count": 0,
        "edge_count": 0,
    }
    return x86_index, {"status": "degraded", "reason": "no_x86_records"}


def _repository_intelligence(
    rig_nodes_list: list[dict[str, Any]],
    rig_edges_list: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    # RIG-002: build rig_index analogously to x86_index. The snapshot metadata
    # (coverage_status, extractor, etc.) lives in the JSON-payload slot and is
    # sourced from the importer (RIG-012). Per DD-011 the JSON payload remains
    # the source-of-truth for replay.
    rig_index = {
        "schema": "codecompass_repository_intelligence.v1",
        "nodes": rig_nodes_list,
        "edges": rig_edges_list,
        "nodes_by_id": {
            n["id"]: n
            for n in rig_nodes_list
            if isinstance(n, dict) and n.get("id")
        },
        "node_count": len(rig_nodes_list),
        "edge_count": len(rig_edges_list),
    }
    diagnostics = {
        "schema": "codecompass_repository_intelligence.v1",
        "node_count": len(rig_nodes_list),
        "edge_count": len(rig_edges_list),
        "status": "ready" if (rig_nodes_list or rig_edges_list) else "degraded",
    }
    if not (rig_nodes_list or rig_edges_list):
        diagnostics["reason"] = "no_rig_records"
    return rig_index, diagnostics
