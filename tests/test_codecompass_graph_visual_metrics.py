from __future__ import annotations

import json
import math
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from worker.retrieval.codecompass_graph_store import CodeCompassGraphStore
from worker.retrieval.codecompass_graph_visual_metrics import (
    build_graph_visual_metrics,
    materialize_graph_visual_metrics,
    verify_visual_metrics_content_hash,
)

ROOT = Path(__file__).resolve().parents[1]
DOMAIN_SCHEMA = json.loads(
    (ROOT / "schemas/artifacts/domain_graph_artifact.v1.json").read_text(encoding="utf-8")
)
METRICS_SCHEMA = json.loads(
    (ROOT / "schemas/artifacts/graph_visual_metrics.v1.json").read_text(encoding="utf-8")
)


def _store(tmp_path: Path) -> CodeCompassGraphStore:
    store = CodeCompassGraphStore(index_path=tmp_path / "cc_graph_index.json")
    store.rebuild_from_output_records(
        manifest_hash="revision-golden-1",
        records=[
            {
                "_provenance": {"output_kind": "graph_nodes"},
                "id": "a",
                "kind": "python_file",
                "file": "/private/repository/a.py",
                "content": "SECRET-FULL-TEXT",
                "line_count": 10,
            },
            {
                "_provenance": {"output_kind": "graph_nodes"},
                "id": "b",
                "kind": "python_function",
                "file": "src/b.py",
                "usage_count": 3,
            },
            {
                "_provenance": {"output_kind": "graph_nodes"},
                "id": "c",
                "kind": "python_function",
                "file": "src/c.py",
            },
            {
                "_provenance": {"output_kind": "graph_edges"},
                "source": "a",
                "target": "b",
                "type": "contains_symbol",
                "confidence": 0,
                "multiplicity": 0,
            },
            {
                "_provenance": {"output_kind": "graph_edges"},
                "source": "a",
                "target": "b",
                "type": "contains_symbol",
            },
            {
                "_provenance": {"output_kind": "graph_edges"},
                "source": "b",
                "target": "c",
                "type": "calls_probable_target",
            },
            {
                "_provenance": {"output_kind": "graph_edges"},
                "source": "a",
                "target": "a",
                "type": "related",
            },
            {
                "_provenance": {"output_kind": "graph_edges"},
                "source": "c",
                "target": "a",
                "type": "child_of_file",
            },
        ],
    )
    return store


def _metrics_by_node(artifact: dict) -> dict[str, dict[str, float | int]]:
    return {row["node_id"]: row["values"] for row in artifact["nodes"]}


def test_domain_graph_schema_keeps_legacy_payload_valid() -> None:
    legacy = {
        "schema": "domain_graph_artifact.v1",
        "source_kind": "legacy",
        "source_ref": "legacy-ref",
        "nodes": [{
            "node_id": "n",
            "node_type": "custom",
            "attributes": {"legacy": True, "domain_id": 42, "metrics": "opaque"},
        }],
        "edges": [{
            "source_id": "n",
            "target_id": "n",
            "relation": "legacy",
            "attributes": {"confidence": "unknown", "multiplicity": "many"},
        }],
        "metadata": {},
    }
    assert list(Draft202012Validator(DOMAIN_SCHEMA).iter_errors(legacy)) == []


def test_worker_artifact_is_schema_valid_deterministic_and_revision_bound(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = materialize_graph_visual_metrics(graph_store=store)
    first_bytes = store.visual_metrics_path.read_bytes()
    second = materialize_graph_visual_metrics(graph_store=store)
    second_bytes = store.visual_metrics_path.read_bytes()

    assert list(Draft202012Validator(METRICS_SCHEMA).iter_errors(first)) == []
    assert first == second
    assert first_bytes == second_bytes
    assert first["graph_revision"] == "revision-golden-1"
    assert verify_visual_metrics_content_hash(first)
    assert first["metric_capabilities"] == dict(sorted(first["metric_capabilities"].items()))
    assert not list(tmp_path.glob(".*.tmp"))


def test_directed_degree_containment_self_loop_and_parallel_edges_are_exact(tmp_path: Path) -> None:
    artifact = materialize_graph_visual_metrics(graph_store=_store(tmp_path))
    values = _metrics_by_node(artifact)

    assert values["a"]["in_degree"] == 2
    assert values["a"]["out_degree"] == 3
    assert values["a"]["total_degree"] == 5
    assert values["a"]["direct_containment_children"] == 2
    assert values["b"]["in_degree"] == 2
    assert values["b"]["out_degree"] == 1
    assert values["b"]["total_degree"] == 3
    assert values["c"]["in_degree"] == 1
    assert values["c"]["out_degree"] == 1
    assert values["c"]["total_degree"] == 2


def test_partial_evidence_is_explicit_and_missing_metrics_have_no_zero_placeholder(tmp_path: Path) -> None:
    artifact = materialize_graph_visual_metrics(graph_store=_store(tmp_path))
    values = _metrics_by_node(artifact)

    assert artifact["metric_capabilities"]["code_extent"]["status"] == "approximate"
    assert artifact["metric_capabilities"]["usage_frequency"]["status"] == "approximate"
    assert artifact["metric_capabilities"]["blast_radius"]["status"] == "unavailable"
    assert artifact["metric_capabilities"]["blast_radius"]["reason_code"] == "seed_scope_not_provided"
    assert values["a"]["code_extent"] == 10
    assert "code_extent" not in values["b"]
    assert values["b"]["usage_frequency"] == 3
    assert "usage_frequency" not in values["a"]
    assert all("blast_radius" not in node_values for node_values in values.values())


def test_advanced_and_seed_scoped_metrics_use_versioned_worker_evidence(tmp_path: Path) -> None:
    artifact = materialize_graph_visual_metrics(
        graph_store=_store(tmp_path),
        include_advanced_metrics=True,
        blast_radius_seeds=("a",),
    )
    values = _metrics_by_node(artifact)

    assert artifact["metric_capabilities"]["degree_centrality"]["status"] == "available"
    assert artifact["metric_capabilities"]["bridge_score"]["status"] == "approximate"
    assert artifact["metric_capabilities"]["bridge_score"]["limits"]["path_cap"] == 1000
    assert artifact["metric_capabilities"]["blast_radius"]["scope"] == "subset"
    assert all(math.isfinite(float(row["degree_centrality"])) for row in values.values())
    assert all(math.isfinite(float(row["bridge_score"])) for row in values.values())
    assert 0 <= values["a"]["blast_radius"] <= 1
    assert "blast_radius" not in values["b"]


def test_missing_graph_store_produces_valid_degraded_artifact(tmp_path: Path) -> None:
    store = CodeCompassGraphStore(index_path=tmp_path / "missing.json")
    artifact = materialize_graph_visual_metrics(graph_store=store)

    assert artifact["nodes"] == []
    assert artifact["metadata"]["node_count"] == 0
    assert artifact["metric_capabilities"]["in_degree"]["status"] == "not_applicable"
    assert list(Draft202012Validator(METRICS_SCHEMA).iter_errors(artifact)) == []


@pytest.mark.parametrize("invalid", [-1, 1.01, float("nan"), float("inf")])
def test_invalid_metric_values_fail_closed(invalid: float) -> None:
    payload = {"state": {"manifest_hash": "r"}, "nodes": [{"id": "n"}], "edges": []}
    with pytest.raises(ValueError, match="invalid_metric_value"):
        build_graph_visual_metrics(
            graph_payload=payload,
            advanced_metrics={"bridge_score": {"n": invalid}},
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("confidence", float("nan")),
        ("confidence", 1.01),
        ("multiplicity", -1),
        ("directed", "yes"),
    ],
)
def test_graph_store_rejects_invalid_edge_visual_values(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    store = CodeCompassGraphStore(index_path=tmp_path / "invalid.json")
    edge = {
        "_provenance": {"output_kind": "graph_edges"},
        "source": "a",
        "target": "b",
        "type": "related",
        field: value,
    }
    with pytest.raises((TypeError, ValueError), match="invalid_graph_edge_value|JSON compliant"):
        store.rebuild_from_output_records(
            manifest_hash="invalid-edge",
            records=[
                {"_provenance": {"output_kind": "graph_nodes"}, "id": "a"},
                {"_provenance": {"output_kind": "graph_nodes"}, "id": "b"},
                edge,
            ],
        )


def test_worker_artifact_excludes_full_text_host_paths_and_timestamps(tmp_path: Path) -> None:
    artifact = materialize_graph_visual_metrics(graph_store=_store(tmp_path))
    serialized = json.dumps(artifact, sort_keys=True)

    assert "SECRET-FULL-TEXT" not in serialized
    assert "/private/repository" not in serialized
    assert "timestamp" not in serialized
    assert "created_at" not in serialized


def test_semantic_edge_round_trip_preserves_visual_evidence_and_identity(tmp_path: Path) -> None:
    store = CodeCompassGraphStore(index_path=tmp_path / "semantic.json")
    store.rebuild_from_output_records(
        manifest_hash="semantic-revision",
        records=[
            {"_provenance": {"output_kind": "graph_nodes"}, "id": "a"},
            {"_provenance": {"output_kind": "graph_nodes"}, "id": "b"},
            {
                "_provenance": {"output_kind": "semantic_edges"},
                "edge_id": "semantic-edge-1",
                "source": "a",
                "target": "b",
                "edge_type": "service_uses_repository",
                "confidence": 0,
                "multiplicity": 0,
                "dependency_weight": 0,
                "directed": False,
                "metrics": {"dependency_weight": 0},
            },
        ],
    )

    edge = store.load()["semantic_edges"][0]
    assert edge["edge_id"] == "semantic-edge-1"
    assert edge["confidence"] == 0
    assert edge["multiplicity"] == 0
    assert edge["dependency_weight"] == 0
    assert edge["directed"] is False
    assert edge["metrics"] == {"dependency_weight": 0}
    artifact = store.load_visual_metrics()
    assert artifact is not None
    values = _metrics_by_node(artifact)
    assert values["a"]["out_degree"] == 1
    assert values["b"]["in_degree"] == 1
    assert artifact["metadata"]["edge_count"] == 1


def test_duplicate_explicit_edge_ids_fail_closed(tmp_path: Path) -> None:
    store = CodeCompassGraphStore(index_path=tmp_path / "duplicate-edge-id.json")
    with pytest.raises(ValueError, match="duplicate_graph_edge_id"):
        store.rebuild_from_output_records(
            manifest_hash="duplicate-edge-id",
            records=[
                {"_provenance": {"output_kind": "graph_nodes"}, "id": "a"},
                {"_provenance": {"output_kind": "graph_nodes"}, "id": "b"},
                {
                    "_provenance": {"output_kind": "graph_edges"},
                    "edge_id": "duplicate",
                    "source": "a", "target": "b", "type": "related", "confidence": 0,
                },
                {
                    "_provenance": {"output_kind": "graph_edges"},
                    "edge_id": "duplicate",
                    "source": "a", "target": "b", "type": "related", "confidence": 1,
                },
            ],
        )


def test_tampering_invalidates_content_hash(tmp_path: Path) -> None:
    artifact = materialize_graph_visual_metrics(graph_store=_store(tmp_path))
    artifact["nodes"][0]["values"]["in_degree"] += 1
    assert not verify_visual_metrics_content_hash(artifact)
