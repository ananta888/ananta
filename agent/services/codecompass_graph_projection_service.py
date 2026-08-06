"""Read-only projection of CodeCompass graph and worker metric artifacts.

The Hub owns validation and API shaping only.  Metric algorithms deliberately
remain outside this module so HTTP requests cannot trigger graph analysis.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from agent.codecompass.semantic_translation.models import (
    EDGE_TYPES as SEMANTIC_TRANSLATION_EDGE_TYPES,
)
from agent.codecompass.semantic_translation.models import (
    NODE_KINDS as SEMANTIC_TRANSLATION_NODE_KINDS,
)

PROJECTION_ALGORITHM_VERSION = "codecompass_graph_projection.v1"
VISUAL_METRICS_ALGORITHM_VERSION = "codecompass_graph_visual_metrics.v1"


@dataclass(frozen=True)
class CodeCompassPreparedEdgePopulation:
    """Revision-scoped identities shared by bounded edge projections."""

    edge_ids: tuple[str, ...]
    parallel_indexes: tuple[int, ...]
    parallel_counts: tuple[int, ...]

    @property
    def size(self) -> int:
        return len(self.edge_ids)

KNOWN_NODE_KINDS = frozenset({
    "aggregator",
    "buildable_component",
    "config",
    "directory",
    "external_package",
    "java_constructor",
    "java_constructor_detail",
    "java_file",
    "java_method",
    "java_method_detail",
    "java_module_summary",
    "java_type",
    "md_file",
    "md_section",
    "package_manager",
    "properties_entry",
    "properties_file",
    "python_class",
    "python_file",
    "python_function",
    "python_import",
    "python_method",
    "python_module_summary",
    "repository",
    "runner",
    "source_file",
    "test",
    "typescript_class",
    "typescript_const",
    "typescript_constructor",
    "typescript_enum",
    "typescript_file",
    "typescript_folder_summary",
    "typescript_function",
    "typescript_import",
    "typescript_interface",
    "typescript_method",
    "typescript_type",
    "wiki_article",
    "wiki_chunk",
    "wiki_section",
    "xml_file",
    "xml_node_detail",
    "xml_tag",
    "yaml_entry",
    "yaml_file",
    *SEMANTIC_TRANSLATION_NODE_KINDS,
})

NODE_KIND_ALIASES = {"ts_file": "typescript_file"}

KNOWN_EDGE_RELATIONS = frozenset({
    "aggregates",
    "bean_factory_method",
    "built_by",
    "calls_probable_target",
    "child_of_file",
    "child_of_type",
    "contains_entry",
    "contains_directory",
    "contains_file",
    "contains_method",
    "contains_section",
    "contains_symbol",
    "contains_type",
    "controller_endpoint_declares",
    "covers",
    "declares_bean",
    "declares_constructor",
    "declares_method",
    "depends_on",
    "extends",
    "field_type_uses",
    "frontend_guard_refs_field",
    "generic_type_uses",
    "implements",
    "imports_module",
    "imports_symbol",
    "injects_dependency",
    "jpa_relation",
    "mapper_maps_type",
    "method_param_type_uses",
    "method_return_type_uses",
    "parent_child",
    "permission_checks_field",
    "policy_applies_to_field",
    "related",
    "returns",
    "runs",
    "tested_by",
    "test_calls_endpoint",
    "test_targets_type",
    "transactional_boundary",
    "uses_type",
    "service_uses_repository",
    "wiki_link",
    *SEMANTIC_TRANSLATION_EDGE_TYPES,
})

_METRIC_NAMES = (
    "blast_radius",
    "bridge_score",
    "code_extent",
    "degree_centrality",
    "descendant_count",
    "direct_containment_children",
    "in_degree",
    "out_degree",
    "total_degree",
    "usage_frequency",
)

_EDGE_METRIC_NAMES = ("confidence", "dependency_weight", "multiplicity")


def _canonical_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _content_hash(payload: Mapping[str, Any]) -> str:
    digest = hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _raw_node_type(node: Mapping[str, Any]) -> str:
    attributes = node.get("attributes") if isinstance(node.get("attributes"), Mapping) else {}
    source_record = node.get("source_record") if isinstance(node.get("source_record"), Mapping) else {}
    value = str(
        node.get("raw_node_type")
        or attributes.get("raw_node_type")
        or source_record.get("kind")
        or source_record.get("type")
        or node.get("node_type")
        or node.get("kind")
        or "unknown"
    )
    return value if value else "unknown"


def _raw_edge_type(edge: Mapping[str, Any]) -> str:
    attributes = edge.get("attributes") if isinstance(edge.get("attributes"), Mapping) else {}
    source_record = edge.get("source_record") if isinstance(edge.get("source_record"), Mapping) else {}
    value = str(
        edge.get("raw_edge_type")
        or attributes.get("raw_edge_type")
        or source_record.get("type")
        or source_record.get("edge_type")
        or edge.get("relation")
        or edge.get("edge_type")
        or edge.get("type")
        or "related"
    )
    return value if value else "related"


def _first_non_blank(values: Sequence[Any]) -> Any | None:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return None


def _provenance_file(record: Mapping[str, Any]) -> Any | None:
    provenance = record.get("provenance")
    if not isinstance(provenance, Mapping):
        return None
    return _first_non_blank((provenance.get("file"),))


def _node_file_and_path(node: Mapping[str, Any]) -> tuple[Any | None, Any | None]:
    attributes = node.get("attributes") if isinstance(node.get("attributes"), Mapping) else {}
    source_record = node.get("source_record") if isinstance(node.get("source_record"), Mapping) else {}
    path = _first_non_blank((
        attributes.get("path"),
        node.get("path"),
        source_record.get("path"),
    ))
    file_path = _first_non_blank((
        attributes.get("file"),
        node.get("file"),
        node.get("path"),
        _provenance_file(node),
        attributes.get("path"),
        _provenance_file(attributes),
        source_record.get("file"),
        source_record.get("path"),
        _provenance_file(source_record),
    ))
    return file_path, path


def _revision_node_attributes(node: Mapping[str, Any]) -> dict[str, Any]:
    candidates: list[Mapping[str, Any]] = [node]
    for field in ("attributes", "attrs", "source_record"):
        value = node.get(field)
        if isinstance(value, Mapping):
            candidates.append(value)
    file_path, path = _node_file_and_path(node)
    projected: dict[str, Any] = {}
    if file_path is not None:
        projected["file"] = file_path
    if path is not None:
        projected["path"] = path
    for key in (
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


def _graph_revision(
    nodes: Sequence[Mapping[str, Any]],
    edges: Sequence[Mapping[str, Any]],
    explicit_revision: str | None,
) -> str:
    revision = str(explicit_revision or "").strip()
    if revision:
        return revision
    node_projection = [
        {
            "node_id": str(item.get("id") or item.get("node_id") or ""),
            "raw_node_type": _raw_node_type(item),
            "visual_attributes": _revision_node_attributes(item),
        }
        for item in nodes
    ]
    edge_projection = []
    for item in edges:
        edge_projection.append({
            "edge_id": str(item.get("edge_id") or ""),
            "source_id": str(item.get("source_id") or item.get("source") or ""),
            "target_id": str(item.get("target_id") or item.get("target") or ""),
            "raw_edge_type": _raw_edge_type(item),
            "visual_attributes": _revision_edge_attributes(item),
        })
    revision_input = {
        "nodes": sorted(
            node_projection,
            key=lambda row: (
                row["node_id"],
                row["raw_node_type"],
                _canonical_json(row["visual_attributes"]),
            ),
        ),
        "edges": sorted(
            edge_projection,
            key=lambda row: (
                row["source_id"],
                row["target_id"],
                row["raw_edge_type"],
                row["edge_id"],
                _canonical_json(row["visual_attributes"]),
            ),
        ),
    }
    return _content_hash(revision_input)


def _unavailable_capabilities(reason_code: str) -> dict[str, dict[str, Any]]:
    return {
        name: {
            "status": "unavailable",
            "source": "codecompass_graph_visual_metrics",
            "algorithm_version": VISUAL_METRICS_ALGORITHM_VERSION,
            "scope": "all_nodes",
            "entity": "node",
            "reason_code": reason_code,
        }
        for name in _METRIC_NAMES
    }


def _edge_metric_capabilities(
    edges: Sequence[Mapping[str, Any]],
    *,
    evidence_graph_revision: str,
) -> dict[str, dict[str, Any]]:
    """Describe intrinsic edge evidence without running graph algorithms."""
    edge_count = len(edges)
    capabilities: dict[str, dict[str, Any]] = {}
    for metric_name in _EDGE_METRIC_NAMES:
        evidence_count = 0
        for edge in edges:
            value = _edge_value(edge, metric_name)
            if value is None:
                attributes = edge.get("attributes")
                if isinstance(attributes, Mapping):
                    metrics = attributes.get("metrics") or attributes.get("visual_metrics")
                    if isinstance(metrics, Mapping):
                        value = metrics.get(metric_name)
                raw_metrics = edge.get("metrics")
                if isinstance(raw_metrics, Mapping) and raw_metrics.get(metric_name) is not None:
                    value = raw_metrics.get(metric_name)
            if _is_finite_non_negative(value):
                evidence_count += 1

        if not edge_count:
            status = "not_applicable"
            scope = "all_edges"
            reason_code = "empty_graph"
        elif evidence_count == edge_count:
            status = "available"
            scope = "all_edges"
            reason_code = None
        elif metric_name in {"confidence", "multiplicity"}:
            status = "approximate"
            scope = "subset" if evidence_count else "all_edges"
            reason_code = (
                "partial_edge_evidence"
                if evidence_count
                else f"{metric_name}_defaulted"
            )
        elif evidence_count:
            status = "approximate"
            scope = "subset"
            reason_code = "partial_edge_evidence"
        else:
            status = "unavailable"
            scope = "all_edges"
            reason_code = "dependency_weight_evidence_missing"

        capability: dict[str, Any] = {
            "status": status,
            "source": "domain_graph_artifact.v1",
            "algorithm_version": PROJECTION_ALGORITHM_VERSION,
            "scope": scope,
            "entity": "edge",
            "graph_revision": evidence_graph_revision,
        }
        if reason_code:
            capability["reason_code"] = reason_code
        if evidence_count != edge_count:
            capability["limits"] = {
                "evidence_edge_count": evidence_count,
                "graph_edge_count": edge_count,
            }
        capabilities[metric_name] = capability
    return capabilities


def _is_finite_non_negative(value: Any) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
        and float(value) >= 0
    )


def _require_finite_non_negative(
    value: Any,
    *,
    field: str,
    maximum: float | None = None,
) -> float | int:
    if (
        not _is_finite_non_negative(value)
        or (maximum is not None and float(value) > maximum)
    ):
        raise ValueError(f"invalid_graph_edge_value:{field}")
    return value


def _invalid_metrics(
    reason_code: str,
    warning: str,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, float | int]], None, str]:
    return _unavailable_capabilities(reason_code), {}, None, warning


def _validated_metrics(
    artifact: Mapping[str, Any] | None,
    *,
    graph_revision: str,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, float | int]], str | None, str | None]:
    if artifact is None:
        return _unavailable_capabilities("worker_metrics_artifact_missing"), {}, None, None
    if artifact.get("schema") != "graph_visual_metrics.v1":
        return _unavailable_capabilities("worker_metrics_schema_invalid"), {}, None, "visual_metrics_schema_invalid"
    artifact_revision = str(artifact.get("graph_revision") or "")
    if not artifact_revision or artifact_revision != graph_revision:
        return _invalid_metrics("worker_metrics_revision_mismatch", "visual_metrics_revision_mismatch")
    expected_hash = str(artifact.get("content_hash") or "")
    unsigned = {key: value for key, value in artifact.items() if key != "content_hash"}
    try:
        hash_matches = bool(expected_hash) and expected_hash == _content_hash(unsigned)
    except (TypeError, ValueError):
        hash_matches = False
    if not hash_matches:
        return _unavailable_capabilities("worker_metrics_hash_invalid"), {}, None, "visual_metrics_hash_invalid"

    if (
        not str(artifact.get("algorithm_version") or "").strip()
        or artifact.get("capability_status") not in {"available", "approximate", "degraded", "unavailable"}
    ):
        return _invalid_metrics("worker_metrics_contract_invalid", "visual_metrics_contract_invalid")

    raw_capabilities = artifact.get("metric_capabilities")
    if not isinstance(raw_capabilities, Mapping):
        return _invalid_metrics("worker_metrics_capabilities_invalid", "visual_metrics_capabilities_invalid")
    capabilities: dict[str, dict[str, Any]] = {}
    valid_statuses = {"available", "approximate", "unavailable", "not_applicable"}
    valid_scopes = {"all_nodes", "subset", "graph"}
    for name, raw_capability in sorted(raw_capabilities.items()):
        if not isinstance(name, str) or not name or not isinstance(raw_capability, Mapping):
            return _invalid_metrics("worker_metrics_capabilities_invalid", "visual_metrics_capabilities_invalid")
        capability = dict(raw_capability)
        if capability.get("status") not in valid_statuses:
            return _invalid_metrics("worker_metrics_capabilities_invalid", "visual_metrics_capabilities_invalid")
        if not str(capability.get("source") or "") or not str(capability.get("algorithm_version") or ""):
            return _invalid_metrics("worker_metrics_capabilities_invalid", "visual_metrics_capabilities_invalid")
        if capability.get("scope") not in valid_scopes:
            return _invalid_metrics("worker_metrics_capabilities_invalid", "visual_metrics_capabilities_invalid")
        if capability.get("entity", "node") != "node":
            return _invalid_metrics("worker_metrics_capabilities_invalid", "visual_metrics_capabilities_invalid")
        if (
            capability["status"] in {"unavailable", "not_applicable"}
            and not str(capability.get("reason_code") or "").strip()
        ):
            return _invalid_metrics("worker_metrics_capabilities_invalid", "visual_metrics_capabilities_invalid")
        capability_revision = str(capability.get("graph_revision") or "").strip()
        if capability_revision and capability_revision != graph_revision:
            return _invalid_metrics("worker_metrics_revision_mismatch", "visual_metrics_revision_mismatch")
        limits = capability.get("limits")
        if limits is not None:
            if not isinstance(limits, Mapping):
                return _invalid_metrics("worker_metrics_capabilities_invalid", "visual_metrics_capabilities_invalid")
            for limit_name, limit_value in limits.items():
                if not isinstance(limit_name, str) or not limit_name:
                    return _invalid_metrics(
                        "worker_metrics_capabilities_invalid",
                        "visual_metrics_capabilities_invalid",
                    )
                if isinstance(limit_value, float) and not math.isfinite(limit_value):
                    return _invalid_metrics(
                        "worker_metrics_capabilities_invalid",
                        "visual_metrics_capabilities_invalid",
                    )
                if limit_value is not None and not isinstance(limit_value, (str, int, float, bool)):
                    return _invalid_metrics(
                        "worker_metrics_capabilities_invalid",
                        "visual_metrics_capabilities_invalid",
                    )
        capability["entity"] = str(capability.get("entity") or "node")
        capabilities[name] = capability

    metrics_by_node: dict[str, dict[str, float | int]] = {}
    rows = artifact.get("nodes")
    if not isinstance(rows, list):
        return _unavailable_capabilities("worker_metrics_values_invalid"), {}, None, "visual_metrics_values_invalid"
    seen_node_ids: set[str] = set()
    for row in rows:
        if not isinstance(row, Mapping):
            return _unavailable_capabilities("worker_metrics_values_invalid"), {}, None, "visual_metrics_values_invalid"
        node_id = str(row.get("node_id") or "").strip()
        values = row.get("values")
        if not node_id or not isinstance(values, Mapping):
            return _unavailable_capabilities("worker_metrics_values_invalid"), {}, None, "visual_metrics_values_invalid"
        if node_id in seen_node_ids:
            return _invalid_metrics("worker_metrics_duplicate_node_id", "visual_metrics_duplicate_node_id")
        seen_node_ids.add(node_id)
        projected: dict[str, float | int] = {}
        for metric_name, value in values.items():
            if not isinstance(metric_name, str) or not metric_name or not _is_finite_non_negative(value):
                return _invalid_metrics("worker_metrics_values_invalid", "visual_metrics_values_invalid")
            projected[metric_name] = value
        metrics_by_node[node_id] = dict(sorted(projected.items()))
    return dict(sorted(capabilities.items())), metrics_by_node, expected_hash, None


def _domain_identity(attributes: Mapping[str, Any], file_path: str) -> tuple[str, str]:
    domain_path = str(attributes.get("domain_path") or "").strip()
    domain_id = str(attributes.get("domain_id") or domain_path).strip()
    if domain_path or domain_id:
        return domain_id or domain_path, domain_path or domain_id
    normalized = file_path.replace("\\", "/").strip("/")
    if not normalized:
        return "", ""
    parent = str(PurePosixPath(normalized).parent)
    if parent == ".":
        parent = ""
    return parent, parent


class CodeCompassGraphProjectionService:
    """Validate evidence and project additive graph fields for API consumers."""

    def project(
        self,
        *,
        nodes: Sequence[Mapping[str, Any]],
        edges: Sequence[Mapping[str, Any]],
        source_kind: str,
        source_ref: str,
        metadata: Mapping[str, Any] | None = None,
        diagnostics: Mapping[str, Any] | None = None,
        warnings: Sequence[str] = (),
        graph_revision: str | None = None,
        visual_metrics: Mapping[str, Any] | None = None,
        derive_projection_revision: bool = False,
        edge_population: Sequence[Mapping[str, Any]] | None = None,
        edge_population_offset: int = 0,
        prepared_edge_population: CodeCompassPreparedEdgePopulation | None = None,
        edge_population_indices: Sequence[int] | None = None,
    ) -> dict[str, Any]:
        evidence_revision = _graph_revision(nodes, edges, graph_revision)
        projection_revision = evidence_revision
        if derive_projection_revision:
            projection_revision = _content_hash({
                "parent_graph_revision": evidence_revision,
                "projection_algorithm_version": PROJECTION_ALGORITHM_VERSION,
                "subgraph_revision": _graph_revision(nodes, edges, None),
            })
        capabilities, metrics_by_node, metrics_hash, metrics_warning = _validated_metrics(
            visual_metrics,
            graph_revision=evidence_revision,
        )
        for capability in capabilities.values():
            capability.setdefault("graph_revision", evidence_revision)
        capabilities.update(_edge_metric_capabilities(
            edges,
            evidence_graph_revision=evidence_revision,
        ))
        capabilities = dict(sorted(capabilities.items()))
        projected_nodes = [self._project_node(node, metrics_by_node) for node in nodes]
        projected_edges = self._project_edges(
            edges,
            edge_population=edge_population,
            edge_population_offset=edge_population_offset,
            prepared_edge_population=prepared_edge_population,
            edge_population_indices=edge_population_indices,
        )
        projected_metadata = {
            **dict(metadata or {}),
            "node_count": len(projected_nodes),
            "edge_count": len(projected_edges),
            "graph_revision": projection_revision,
            "evidence_graph_revision": evidence_revision,
            "projection_algorithm_version": PROJECTION_ALGORITHM_VERSION,
        }
        if projection_revision != evidence_revision:
            projected_metadata["parent_graph_revision"] = evidence_revision
        if metrics_hash:
            projected_metadata["visual_metrics_content_hash"] = metrics_hash
        projected_warnings = [str(item) for item in warnings if str(item)]
        if metrics_warning and metrics_warning not in projected_warnings:
            projected_warnings.append(metrics_warning)
        result: dict[str, Any] = {
            "schema": "domain_graph_artifact.v1",
            "source_kind": str(source_kind),
            "source_ref": str(source_ref),
            "nodes": projected_nodes,
            "edges": projected_edges,
            "metadata": projected_metadata,
            "metric_capabilities": capabilities,
            "warnings": projected_warnings,
        }
        if diagnostics is not None:
            result["diagnostics"] = dict(diagnostics)
        return result

    @staticmethod
    def _project_node(
        node: Mapping[str, Any],
        metrics_by_node: Mapping[str, Mapping[str, float | int]],
    ) -> dict[str, Any]:
        node_id = str(node.get("node_id") or node.get("id") or "")
        source_attributes = node.get("attributes") if isinstance(node.get("attributes"), Mapping) else {}
        source_record = node.get("source_record") if isinstance(node.get("source_record"), Mapping) else {}
        attributes = dict(source_attributes)
        file_path, path = _node_file_and_path(node)
        if file_path is not None:
            attributes["file"] = file_path
        if path is not None:
            attributes["path"] = path
        if not str(attributes.get("name") or "").strip():
            name = _first_non_blank((
                node.get("name"),
                node.get("symbol"),
                source_record.get("name"),
                source_record.get("symbol"),
            ))
            if name is not None:
                attributes["name"] = name
        for output_key, candidates in {
            "content": (node.get("content"), source_record.get("content"), source_record.get("summary")),
            "record_id": (node.get("record_id"), source_record.get("record_id")),
            "importance_score": (node.get("importance_score"), source_record.get("importance_score")),
            "domain_id": (node.get("domain_id"), source_record.get("domain_id")),
            "domain_path": (node.get("domain_path"), source_record.get("domain_path")),
            "domain_level": (node.get("domain_level"), source_record.get("domain_level")),
            "domain_parent": (node.get("domain_parent"), source_record.get("domain_parent")),
            "domain_leaf": (node.get("domain_leaf"), source_record.get("domain_leaf")),
        }.items():
            if output_key in attributes:
                continue
            for value in candidates:
                if value is not None:
                    attributes[output_key] = value
                    break
        attributes.setdefault("file", "")
        attributes.setdefault("name", "")
        attributes.setdefault("content", "")
        attributes.setdefault("record_id", node_id)

        raw_type = _raw_node_type(node)
        normalized_type = raw_type.strip().lower()
        canonical_type = NODE_KIND_ALIASES.get(normalized_type, normalized_type)
        is_known = canonical_type in KNOWN_NODE_KINDS
        attributes["raw_node_type"] = raw_type
        attributes["known_kind"] = canonical_type if is_known else "unknown"
        attributes["semantic_status"] = "known" if is_known else "semantically_unknown"
        attributes["visual_fallback"] = canonical_type if is_known else "unknown"
        domain_id, domain_path = _domain_identity(attributes, str(attributes.get("file") or ""))
        attributes["domain_id"] = domain_id
        attributes["domain_path"] = domain_path

        existing_metrics = attributes.get("metrics") if isinstance(attributes.get("metrics"), Mapping) else {}
        projected_metrics = {
            str(name): value
            for name, value in existing_metrics.items()
            if isinstance(name, str) and _is_finite_non_negative(value)
        }
        projected_metrics.update(metrics_by_node.get(node_id) or {})
        if projected_metrics:
            attributes["metrics"] = dict(sorted(projected_metrics.items()))
        else:
            attributes.pop("metrics", None)
        return {
            "node_id": node_id,
            "node_type": canonical_type if is_known else "unknown",
            "attributes": attributes,
        }

    @staticmethod
    def prepare_edge_population(
        edges: Sequence[Mapping[str, Any]],
    ) -> CodeCompassPreparedEdgePopulation:
        """Prepare globally stable edge identity facts exactly once per revision."""

        parallel_keys: list[tuple[str, str, str]] = []
        signatures: list[str] = []
        explicit_ids: list[str] = []
        for edge in edges:
            source_id = str(edge.get("source_id") or edge.get("source") or "")
            target_id = str(edge.get("target_id") or edge.get("target") or "")
            raw_relation = _raw_edge_type(edge)
            parallel_keys.append((source_id, target_id, raw_relation))
            explicit_ids.append(str(edge.get("edge_id") or "").strip())
            signatures.append(_canonical_json({
                "source_id": source_id,
                "target_id": target_id,
                "raw_edge_type": raw_relation,
                "visual_attributes": _revision_edge_attributes(edge),
                "field": edge.get("field"),
                "operation": edge.get("operation"),
                "heuristic": edge.get("heuristic"),
            }))
        parallel_counts_by_key = Counter(parallel_keys)
        parallel_indexes = [0] * len(parallel_keys)
        indexes_by_key: dict[tuple[str, str, str], list[int]] = {}
        for index, key in enumerate(parallel_keys):
            indexes_by_key.setdefault(key, []).append(index)
        for indexes in indexes_by_key.values():
            for parallel_index, index in enumerate(sorted(
                indexes,
                key=lambda item: (explicit_ids[item], signatures[item], item),
            )):
                parallel_indexes[index] = parallel_index

        explicit_counts = Counter(item for item in explicit_ids if item)
        duplicate_explicit_id = next(
            (edge_id for edge_id, count in explicit_counts.items() if count > 1),
            None,
        )
        if duplicate_explicit_id is not None:
            raise ValueError("duplicate_graph_edge_id")
        identity_occurrence: Counter[str] = Counter()
        edge_ids: list[str] = []
        for index, explicit_id in enumerate(explicit_ids):
            if explicit_id and explicit_counts[explicit_id] == 1:
                edge_identity = explicit_id
            else:
                identity_seed = _content_hash({
                    "source_edge_id": explicit_id,
                    "signature": signatures[index],
                })
                duplicate_index = identity_occurrence[identity_seed]
                identity_occurrence[identity_seed] += 1
                edge_identity = (
                    identity_seed
                    if duplicate_index == 0
                    else _content_hash({
                        "identity_seed": identity_seed,
                        "duplicate_index": duplicate_index,
                    })
                )
            edge_ids.append(edge_identity)
        return CodeCompassPreparedEdgePopulation(
            edge_ids=tuple(edge_ids),
            parallel_indexes=tuple(parallel_indexes),
            parallel_counts=tuple(
                parallel_counts_by_key[key] for key in parallel_keys
            ),
        )

    @classmethod
    def _project_edges(
        cls,
        edges: Sequence[Mapping[str, Any]],
        *,
        edge_population: Sequence[Mapping[str, Any]] | None = None,
        edge_population_offset: int = 0,
        prepared_edge_population: CodeCompassPreparedEdgePopulation | None = None,
        edge_population_indices: Sequence[int] | None = None,
    ) -> list[dict[str, Any]]:
        if prepared_edge_population is not None and edge_population is not None:
            raise ValueError("graph_edge_population_ambiguous")
        prepared = prepared_edge_population
        if prepared is None:
            population = edge_population if edge_population is not None else edges
            prepared = cls.prepare_edge_population(population)
        if edge_population_indices is not None:
            population_indices = tuple(int(index) for index in edge_population_indices)
            if len(population_indices) != len(edges):
                raise ValueError("invalid_graph_edge_population_window")
        else:
            population_offset = int(edge_population_offset)
            population_indices = tuple(
                range(population_offset, population_offset + len(edges))
            )
        if any(index < 0 or index >= prepared.size for index in population_indices):
            raise ValueError("invalid_graph_edge_population_window")
        projected: list[dict[str, Any]] = []
        for edge, population_index in zip(edges, population_indices):
            source_id = str(edge.get("source_id") or edge.get("source") or "")
            target_id = str(edge.get("target_id") or edge.get("target") or "")
            raw_relation = _raw_edge_type(edge)
            source_attributes = edge.get("attributes") if isinstance(edge.get("attributes"), Mapping) else {}
            attributes = dict(source_attributes)
            for field in ("field", "operation", "heuristic"):
                if field not in attributes and edge.get(field) is not None:
                    attributes[field] = edge[field]
            normalized_relation = raw_relation.strip().lower()
            is_known = normalized_relation in KNOWN_EDGE_RELATIONS
            attributes["raw_edge_type"] = raw_relation
            attributes["known_relation"] = normalized_relation if is_known else "related"
            attributes["semantic_status"] = "known" if is_known else "semantically_unknown"
            attributes["visual_fallback"] = normalized_relation if is_known else "related"
            confidence = edge.get("confidence")
            if confidence is None:
                confidence = attributes.get("confidence")
            if confidence is None:
                confidence = 1.0
            attributes["confidence"] = _require_finite_non_negative(
                confidence,
                field="confidence",
                maximum=1,
            )
            multiplicity = edge.get("multiplicity")
            if multiplicity is None:
                multiplicity = attributes.get("multiplicity")
            if multiplicity is None:
                multiplicity = 1
            attributes["multiplicity"] = _require_finite_non_negative(
                multiplicity,
                field="multiplicity",
            )
            directed = edge.get("directed")
            if directed is None:
                directed = attributes.get("directed")
            if directed is not None and not isinstance(directed, bool):
                raise ValueError("invalid_graph_edge_value:directed")
            attributes["directed"] = True if directed is None else directed
            attributes["self_loop"] = source_id == target_id
            attributes["parallel_index"] = prepared.parallel_indexes[population_index]
            attributes["parallel_count"] = prepared.parallel_counts[population_index]
            dependency_weight = _edge_value(edge, "dependency_weight")
            raw_metrics = attributes.get("metrics") or attributes.get("visual_metrics")
            metrics = dict(raw_metrics) if isinstance(raw_metrics, Mapping) else {}
            edge_metrics = edge.get("metrics")
            if isinstance(edge_metrics, Mapping):
                metrics.update(edge_metrics)
            if dependency_weight is not None:
                metrics.setdefault("dependency_weight", dependency_weight)
            if metrics:
                attributes["metrics"] = dict(sorted(
                    (
                        str(metric_name),
                        _require_finite_non_negative(
                            metric_value,
                            field=f"metrics.{metric_name}",
                        ),
                    )
                    for metric_name, metric_value in metrics.items()
                ))
            attributes.pop("visual_metrics", None)

            projected.append({
                "edge_id": prepared.edge_ids[population_index],
                "source_id": source_id,
                "target_id": target_id,
                "relation": raw_relation,
                "attributes": attributes,
            })
        return projected


codecompass_graph_projection_service = CodeCompassGraphProjectionService()


def get_codecompass_graph_projection_service() -> CodeCompassGraphProjectionService:
    return codecompass_graph_projection_service


__all__ = [
    "CodeCompassPreparedEdgePopulation",
    "CodeCompassGraphProjectionService",
    "KNOWN_EDGE_RELATIONS",
    "KNOWN_NODE_KINDS",
    "PROJECTION_ALGORITHM_VERSION",
    "get_codecompass_graph_projection_service",
]
