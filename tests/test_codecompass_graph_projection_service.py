from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from agent.codecompass.semantic_translation.models import (
    EDGE_TYPES as SEMANTIC_TRANSLATION_EDGE_TYPES,
)
from agent.codecompass.semantic_translation.models import (
    NODE_KINDS as SEMANTIC_TRANSLATION_NODE_KINDS,
)
from agent.services.codecompass_graph_projection_service import (
    KNOWN_EDGE_RELATIONS,
    KNOWN_NODE_KINDS,
    CodeCompassGraphProjectionService,
    _content_hash,
)
from worker.retrieval.codecompass_graph_store import CodeCompassGraphStore
from worker.retrieval.codecompass_graph_visual_metrics import build_graph_visual_metrics

ROOT = Path(__file__).resolve().parents[1]
DOMAIN_SCHEMA = json.loads(
    (ROOT / "schemas/artifacts/domain_graph_artifact.v1.json").read_text(encoding="utf-8")
)


def _payload() -> dict:
    return {
        "state": {"manifest_hash": "projection-revision-1"},
        "nodes": [
            {
                "id": "known",
                "kind": "python_function",
                "raw_node_type": "python_function",
                "file": "agent/services/known.py",
                "name": "known",
            },
            {
                "id": "unknown",
                "kind": "vendor::node",
                "raw_node_type": "Vendor::Node",
                "file": "plugins/vendor/custom.ext",
                "name": "custom",
            },
        ],
        "edges": [
            {
                "source_id": "known",
                "target_id": "unknown",
                "edge_type": "vendor::relation",
                "raw_edge_type": "Vendor::Relation",
                "confidence": 0.0,
                "multiplicity": 0,
            },
            {
                "source_id": "known",
                "target_id": "unknown",
                "edge_type": "vendor::relation",
                "raw_edge_type": "Vendor::Relation",
                "confidence": 1.0,
            },
            {
                "source_id": "unknown",
                "target_id": "unknown",
                "edge_type": "related",
                "raw_edge_type": "related",
                "confidence": 1.0,
            },
        ],
    }


def _project(payload: dict, *, metrics: dict | None) -> dict:
    return CodeCompassGraphProjectionService().project(
        nodes=payload["nodes"],
        edges=payload["edges"],
        source_kind="codecompass_graph",
        source_ref="idx-1",
        graph_revision=payload["state"]["manifest_hash"],
        visual_metrics=metrics,
        metadata={"knowledge_index_id": "idx-1"},
    )


def test_full_projection_is_schema_valid_and_additive() -> None:
    payload = _payload()
    metrics = build_graph_visual_metrics(graph_payload=payload)
    result = _project(payload, metrics=metrics)

    assert list(Draft202012Validator(DOMAIN_SCHEMA).iter_errors(result)) == []
    assert result["schema"] == "domain_graph_artifact.v1"
    assert result["source_ref"] == "idx-1"
    assert result["metadata"]["knowledge_index_id"] == "idx-1"
    assert result["metadata"]["graph_revision"] == "projection-revision-1"
    assert result["metadata"]["visual_metrics_content_hash"] == metrics["content_hash"]
    assert result["metric_capabilities"]["in_degree"]["status"] == "available"
    assert result["metric_capabilities"]["in_degree"]["entity"] == "node"
    assert result["metric_capabilities"]["confidence"]["entity"] == "edge"
    assert result["nodes"][0]["attributes"]["metrics"]["out_degree"] == 2


def test_unknown_semantics_keep_raw_values_and_only_use_visual_fallback() -> None:
    payload = _payload()
    result = _project(payload, metrics=build_graph_visual_metrics(graph_payload=payload))
    node = next(item for item in result["nodes"] if item["node_id"] == "unknown")
    edge = result["edges"][0]

    assert node["node_type"] == "unknown"
    assert node["attributes"]["raw_node_type"] == "Vendor::Node"
    assert node["attributes"]["known_kind"] == "unknown"
    assert node["attributes"]["semantic_status"] == "semantically_unknown"
    assert edge["relation"] == "Vendor::Relation"
    assert edge["attributes"]["raw_edge_type"] == "Vendor::Relation"
    assert edge["attributes"]["known_relation"] == "related"
    assert edge["attributes"]["semantic_status"] == "semantically_unknown"


def test_repository_bridge_topology_semantics_are_known_without_weakening_unknown_fallback() -> None:
    result = CodeCompassGraphProjectionService().project(
        nodes=[
            {
                "id": "repo",
                "kind": "repository",
                "path": "repositories/ananta",
                "domain_id": "repository-root",
                "domain_path": "repositories/ananta",
            },
            {
                "id": "directory",
                "kind": "directory",
                "path": "src/orders",
            },
            {
                "id": "file",
                "kind": "source_file",
                "path": "src/orders/service.py",
            },
            {
                "id": "semantic",
                "kind": "semantic_node",
                "symbol": "BillingModel",
                "provenance": {"file": "src/billing/model.py"},
            },
            {
                "id": "unknown",
                "kind": "vendor::node",
                "raw_node_type": "Vendor::Node",
            },
        ],
        edges=[
            {
                "source_id": "repo",
                "target_id": "directory",
                "edge_type": "contains_directory",
            },
            {
                "source_id": "directory",
                "target_id": "file",
                "edge_type": "contains_file",
            },
            {
                "source_id": "file",
                "target_id": "unknown",
                "raw_edge_type": "Vendor::Relation",
            },
        ],
        source_kind="repository_bridge",
        source_ref="idx-repository",
    )

    nodes = {node["node_id"]: node for node in result["nodes"]}
    for node_id, expected_kind in {
        "repo": "repository",
        "directory": "directory",
        "file": "source_file",
        "semantic": "semantic_node",
    }.items():
        node = nodes[node_id]
        assert node["node_type"] == expected_kind
        assert node["attributes"]["known_kind"] == expected_kind
        assert node["attributes"]["semantic_status"] == "known"
        assert node["attributes"]["visual_fallback"] == expected_kind

    assert nodes["repo"]["attributes"]["path"] == "repositories/ananta"
    assert nodes["repo"]["attributes"]["file"] == "repositories/ananta"
    assert nodes["repo"]["attributes"]["domain_id"] == "repository-root"
    assert nodes["repo"]["attributes"]["domain_path"] == "repositories/ananta"
    assert nodes["directory"]["attributes"]["path"] == "src/orders"
    assert nodes["directory"]["attributes"]["file"] == "src/orders"
    assert nodes["directory"]["attributes"]["domain_id"] == "src"
    assert nodes["file"]["attributes"]["path"] == "src/orders/service.py"
    assert nodes["file"]["attributes"]["file"] == "src/orders/service.py"
    assert nodes["file"]["attributes"]["domain_id"] == "src/orders"
    assert nodes["semantic"]["attributes"]["file"] == "src/billing/model.py"
    assert nodes["semantic"]["attributes"]["domain_id"] == "src/billing"
    assert nodes["semantic"]["attributes"]["name"] == "BillingModel"

    edges = {edge["relation"]: edge for edge in result["edges"]}
    for relation in ("contains_directory", "contains_file"):
        edge = edges[relation]
        assert edge["attributes"]["known_relation"] == relation
        assert edge["attributes"]["semantic_status"] == "known"
        assert edge["attributes"]["visual_fallback"] == relation

    unknown_node = nodes["unknown"]
    assert unknown_node["node_type"] == "unknown"
    assert unknown_node["attributes"]["raw_node_type"] == "Vendor::Node"
    assert unknown_node["attributes"]["semantic_status"] == "semantically_unknown"
    assert unknown_node["attributes"]["visual_fallback"] == "unknown"

    unknown_edge = edges["Vendor::Relation"]
    assert unknown_edge["attributes"]["raw_edge_type"] == "Vendor::Relation"
    assert unknown_edge["attributes"]["semantic_status"] == "semantically_unknown"
    assert unknown_edge["attributes"]["visual_fallback"] == "related"


def test_all_canonical_semantic_translation_types_are_registered() -> None:
    assert SEMANTIC_TRANSLATION_NODE_KINDS <= KNOWN_NODE_KINDS
    assert SEMANTIC_TRANSLATION_EDGE_TYPES <= KNOWN_EDGE_RELATIONS

    nodes = [
        {"id": f"semantic-kind:{kind}", "kind": kind}
        for kind in sorted(SEMANTIC_TRANSLATION_NODE_KINDS)
    ]
    source_id = str(nodes[0]["id"])
    target_id = str(nodes[-1]["id"])
    result = CodeCompassGraphProjectionService().project(
        nodes=nodes,
        edges=[
            {
                "source_id": source_id,
                "target_id": target_id,
                "edge_type": edge_type,
            }
            for edge_type in sorted(SEMANTIC_TRANSLATION_EDGE_TYPES)
        ],
        source_kind="semantic_translation",
        source_ref="canonical-vocabulary",
    )

    assert {
        node["node_type"]
        for node in result["nodes"]
        if node["attributes"]["semantic_status"] == "known"
    } >= SEMANTIC_TRANSLATION_NODE_KINDS
    assert {
        edge["attributes"]["known_relation"]
        for edge in result["edges"]
        if edge["attributes"]["semantic_status"] == "known"
    } >= SEMANTIC_TRANSLATION_EDGE_TYPES


def test_existing_file_identity_precedes_additive_path_and_provenance_fallbacks() -> None:
    result = CodeCompassGraphProjectionService().project(
        nodes=[
            {
                "id": "existing-attributes",
                "kind": "source_file",
                "file": "top-level/ignored.py",
                "path": "top-level/path-ignored.py",
                "attributes": {
                    "file": "canonical/existing.py",
                    "path": "canonical/existing-path.py",
                },
                "provenance": {"file": "provenance/ignored.py"},
            },
            {
                "id": "source-record",
                "kind": "source_file",
                "source_record": {
                    "file": "canonical/source-record.py",
                    "path": "source-record/path-ignored.py",
                },
            },
        ],
        edges=[],
        source_kind="compatibility",
        source_ref="file-identity",
    )

    nodes = {node["node_id"]: node for node in result["nodes"]}
    existing = nodes["existing-attributes"]["attributes"]
    assert existing["file"] == "canonical/existing.py"
    assert existing["path"] == "canonical/existing-path.py"
    assert existing["domain_id"] == "canonical"
    source_record = nodes["source-record"]["attributes"]
    assert source_record["file"] == "canonical/source-record.py"
    assert source_record["path"] == "source-record/path-ignored.py"
    assert source_record["domain_id"] == "canonical"


def test_raw_types_are_preserved_byte_for_byte_while_classification_is_normalized() -> None:
    payload = _payload()
    payload["nodes"][0]["raw_node_type"] = " python_function "
    payload["edges"][0]["raw_edge_type"] = " Vendor::Relation "
    result = _project(payload, metrics=build_graph_visual_metrics(graph_payload=payload))

    node = next(item for item in result["nodes"] if item["node_id"] == "known")
    edge = result["edges"][0]
    assert node["attributes"]["raw_node_type"] == " python_function "
    assert node["attributes"]["known_kind"] == "python_function"
    assert edge["attributes"]["raw_edge_type"] == " Vendor::Relation "
    assert edge["attributes"]["semantic_status"] == "semantically_unknown"


def test_architecture_fixture_types_have_registered_semantics(tmp_path: Path) -> None:
    fixture = json.loads(
        (ROOT / "tests/fixtures/codecompass_architecture/graph_records.json").read_text(encoding="utf-8")
    )
    store = CodeCompassGraphStore(index_path=tmp_path / "architecture.json")
    store.rebuild_from_output_records(
        records=fixture["records"],
        manifest_hash=fixture["manifest_hash"],
    )
    payload = store.load()
    result = CodeCompassGraphProjectionService().project(
        nodes=payload["nodes"],
        edges=payload["edges"],
        source_kind="architecture_fixture",
        source_ref="unverified",
        graph_revision=fixture["manifest_hash"],
        visual_metrics=store.load_visual_metrics(),
    )

    ts_node = next(node for node in result["nodes"] if node["attributes"]["raw_node_type"] == "ts_file")
    assert ts_node["node_type"] == "typescript_file"
    assert ts_node["attributes"]["known_kind"] == "typescript_file"
    assert all(edge["attributes"]["semantic_status"] == "known" for edge in result["edges"])


def test_zero_confidence_zero_multiplicity_parallel_edges_and_self_loops_survive() -> None:
    payload = _payload()
    result = _project(payload, metrics=build_graph_visual_metrics(graph_payload=payload))
    first, second, self_loop = result["edges"]

    assert first["attributes"]["confidence"] == 0
    assert first["attributes"]["multiplicity"] == 0
    assert second["attributes"]["multiplicity"] == 1
    assert first["attributes"]["parallel_index"] == 0
    assert second["attributes"]["parallel_index"] == 1
    assert first["attributes"]["parallel_count"] == 2
    assert second["attributes"]["parallel_count"] == 2
    assert first["edge_id"] != second["edge_id"]
    assert self_loop["attributes"]["self_loop"] is True
    assert self_loop["attributes"]["directed"] is True


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("confidence", float("nan")),
        ("confidence", float("inf")),
        ("confidence", 1.01),
        ("multiplicity", -1),
        ("dependency_weight", float("inf")),
        ("directed", "yes"),
    ],
)
def test_invalid_edge_visual_values_fail_closed(field: str, value: object) -> None:
    payload = _payload()
    payload["edges"][0][field] = value

    with pytest.raises((TypeError, ValueError)):
        _project(payload, metrics=None)


def test_missing_metrics_use_stable_unavailable_capabilities_without_values() -> None:
    payload = _payload()
    result = _project(payload, metrics=None)

    assert result["metric_capabilities"]["in_degree"] == {
        "status": "unavailable",
        "source": "codecompass_graph_visual_metrics",
        "algorithm_version": "codecompass_graph_visual_metrics.v1",
        "scope": "all_nodes",
        "entity": "node",
        "graph_revision": "projection-revision-1",
        "reason_code": "worker_metrics_artifact_missing",
    }
    assert all("metrics" not in node["attributes"] for node in result["nodes"])


def test_revision_mismatch_and_hash_tampering_fail_closed() -> None:
    payload = _payload()
    mismatch = build_graph_visual_metrics(graph_payload=payload)
    mismatch["graph_revision"] = "different"
    mismatch_result = _project(payload, metrics=mismatch)
    assert mismatch_result["metric_capabilities"]["in_degree"]["reason_code"] == "worker_metrics_revision_mismatch"
    assert "visual_metrics_revision_mismatch" in mismatch_result["warnings"]

    tampered = build_graph_visual_metrics(graph_payload=payload)
    tampered["nodes"][0]["values"]["in_degree"] = 999
    tampered_result = _project(payload, metrics=tampered)
    assert tampered_result["metric_capabilities"]["in_degree"]["reason_code"] == "worker_metrics_hash_invalid"
    assert "visual_metrics_hash_invalid" in tampered_result["warnings"]


def test_invalid_worker_capability_and_duplicate_metric_rows_fail_closed() -> None:
    payload = _payload()
    invalid_capability = build_graph_visual_metrics(graph_payload=payload)
    invalid_capability["metric_capabilities"]["in_degree"]["scope"] = "all_edges"
    unsigned = {key: value for key, value in invalid_capability.items() if key != "content_hash"}
    invalid_capability["content_hash"] = _content_hash(unsigned)

    invalid_result = _project(payload, metrics=invalid_capability)
    assert invalid_result["metric_capabilities"]["in_degree"]["reason_code"] == (
        "worker_metrics_capabilities_invalid"
    )
    assert "visual_metrics_capabilities_invalid" in invalid_result["warnings"]

    duplicate_rows = build_graph_visual_metrics(graph_payload=payload)
    duplicate_rows["nodes"].append(dict(duplicate_rows["nodes"][0]))
    unsigned = {key: value for key, value in duplicate_rows.items() if key != "content_hash"}
    duplicate_rows["content_hash"] = _content_hash(unsigned)

    duplicate_result = _project(payload, metrics=duplicate_rows)
    assert duplicate_result["metric_capabilities"]["in_degree"]["reason_code"] == (
        "worker_metrics_duplicate_node_id"
    )
    assert "visual_metrics_duplicate_node_id" in duplicate_result["warnings"]


def test_normal_and_self_graph_use_the_same_optional_contract_shape() -> None:
    payload = _payload()
    service = CodeCompassGraphProjectionService()
    normal = service.project(
        nodes=payload["nodes"],
        edges=payload["edges"],
        source_kind="codecompass_graph",
        source_ref="idx",
    )
    self_graph = service.project(
        nodes=payload["nodes"],
        edges=payload["edges"],
        source_kind="ananta_self_graph",
        source_ref="ananta",
    )

    assert set(normal) == set(self_graph)
    assert set(normal["metadata"]) == set(self_graph["metadata"])
    assert normal["metric_capabilities"] == self_graph["metric_capabilities"]
    assert [item["attributes"]["domain_id"] for item in normal["nodes"]] == [
        item["attributes"]["domain_id"] for item in self_graph["nodes"]
    ]


def test_fallback_revision_changes_for_style_relevant_node_attributes() -> None:
    payload = _payload()
    payload["nodes"][0]["importance_score"] = 0.25
    service = CodeCompassGraphProjectionService()
    first = service.project(
        nodes=payload["nodes"],
        edges=payload["edges"],
        source_kind="ananta_self_graph",
        source_ref="ananta",
    )
    payload["nodes"][0]["importance_score"] = 0.75
    second = service.project(
        nodes=payload["nodes"],
        edges=payload["edges"],
        source_kind="ananta_self_graph",
        source_ref="ananta",
    )
    payload["nodes"][0]["domain_path"] = "agent.changed"
    third = service.project(
        nodes=payload["nodes"],
        edges=payload["edges"],
        source_kind="ananta_self_graph",
        source_ref="ananta",
    )

    assert first["metadata"]["graph_revision"] != second["metadata"]["graph_revision"]
    assert second["metadata"]["graph_revision"] != third["metadata"]["graph_revision"]


def test_fallback_revision_covers_all_style_relevant_edge_evidence() -> None:
    payload = _payload()
    payload["state"] = {}
    service = CodeCompassGraphProjectionService()
    first = service.project(
        nodes=payload["nodes"], edges=payload["edges"],
        source_kind="ananta_self_graph", source_ref="ananta",
    )
    payload["edges"][0]["dependency_weight"] = 0
    second = service.project(
        nodes=payload["nodes"], edges=payload["edges"],
        source_kind="ananta_self_graph", source_ref="ananta",
    )
    payload["edges"][0]["directed"] = False
    third = service.project(
        nodes=payload["nodes"], edges=payload["edges"],
        source_kind="ananta_self_graph", source_ref="ananta",
    )

    assert first["metadata"]["graph_revision"] != second["metadata"]["graph_revision"]
    assert second["metadata"]["graph_revision"] != third["metadata"]["graph_revision"]
    assert second["edges"][0]["attributes"]["metrics"]["dependency_weight"] == 0


def test_edge_identity_is_order_independent_and_preserves_explicit_ids() -> None:
    payload = _payload()
    payload["edges"][0]["edge_id"] = "provided-edge-id"
    service = CodeCompassGraphProjectionService()
    first = service.project(
        nodes=payload["nodes"], edges=payload["edges"],
        source_kind="graph", source_ref="ref",
    )
    reordered = service.project(
        nodes=payload["nodes"], edges=list(reversed(payload["edges"])),
        source_kind="graph", source_ref="ref",
    )

    def identity_by_confidence(result: dict) -> dict[float, str]:
        return {
            float(edge["attributes"]["confidence"]): edge["edge_id"]
            for edge in result["edges"]
            if edge["source_id"] == "known" and edge["target_id"] == "unknown"
        }

    assert identity_by_confidence(first) == identity_by_confidence(reordered)
    assert identity_by_confidence(first)[0.0] == "provided-edge-id"


def test_projection_revision_is_scoped_but_worker_evidence_revision_is_preserved() -> None:
    payload = _payload()
    metrics = build_graph_visual_metrics(graph_payload=payload)
    result = CodeCompassGraphProjectionService().project(
        nodes=payload["nodes"][:1],
        edges=[],
        source_kind="codecompass_graph_expansion",
        source_ref="idx:profile:seed",
        graph_revision=payload["state"]["manifest_hash"],
        visual_metrics=metrics,
        derive_projection_revision=True,
    )

    assert result["metadata"]["graph_revision"] != payload["state"]["manifest_hash"]
    assert result["metadata"]["evidence_graph_revision"] == payload["state"]["manifest_hash"]
    assert result["metadata"]["parent_graph_revision"] == payload["state"]["manifest_hash"]
    assert result["metric_capabilities"]["in_degree"]["graph_revision"] == payload["state"]["manifest_hash"]


def test_hub_fallback_revision_matches_worker_revision_without_manifest() -> None:
    payload = _payload()
    payload["state"] = {}
    payload["nodes"][0]["importance_score"] = 0.5
    metrics = build_graph_visual_metrics(graph_payload=payload)
    result = CodeCompassGraphProjectionService().project(
        nodes=payload["nodes"],
        edges=payload["edges"],
        source_kind="codecompass_graph",
        source_ref="idx",
        visual_metrics=metrics,
    )

    assert result["metadata"]["graph_revision"] == metrics["graph_revision"]
    assert result["metadata"]["visual_metrics_content_hash"] == metrics["content_hash"]


def test_projection_service_has_no_worker_algorithm_imports() -> None:
    path = ROOT / "agent/services/codecompass_graph_projection_service.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.append(node.module or "")
    assert not any(name.startswith("worker.retrieval") for name in imports)
