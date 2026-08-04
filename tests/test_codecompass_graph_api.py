from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from jsonschema import Draft202012Validator

from agent.services.codecompass_graph_artifact_resolver import (
    CodeCompassGraphArtifactResolver,
)
from agent.services.knowledge_index_consumption_policy import (
    KNOWLEDGE_INDEX_EXECUTION_BINDING_METADATA_KEY,
    KNOWLEDGE_INDEX_EXECUTION_JOB_SCHEMA,
    KNOWLEDGE_INDEX_MATERIALIZATION_BINDING_SCHEMA,
)
from worker.retrieval.codecompass_graph_store import CodeCompassGraphStore

# ── helpers ───────────────────────────────────────────────────────────────────

def _build_graph_index(tmp_path: Path) -> Path:
    index_path = tmp_path / "cc_graph_index.json"
    store = CodeCompassGraphStore(index_path=index_path)
    store.rebuild_from_output_records(
        records=[
            {
                "id": "n1",
                "kind": "java_type",
                "name": "OrderService",
                "file": "src/OrderService.java",
                "content": "Handles order processing",
                "_provenance": {"output_kind": "graph_nodes"},
            },
            {
                "id": "n2",
                "kind": "java_method",
                "name": "placeOrder",
                "file": "src/OrderService.java",
                "content": "Places a new order",
                "_provenance": {"output_kind": "graph_nodes"},
            },
            {
                "id": "n3",
                "kind": "config",
                "name": "application.yml",
                "file": "src/main/resources/application.yml",
                "content": "App configuration",
                "_provenance": {"output_kind": "graph_nodes"},
            },
            {
                "source": "n2",
                "target": "n1",
                "type": "child_of_type",
                "confidence": 1.0,
                "_provenance": {"output_kind": "graph_edges"},
            },
            {
                "source": "n1",
                "target": "n3",
                "type": "injects_dependency",
                "confidence": 0.9,
                "_provenance": {"output_kind": "graph_edges"},
            },
        ],
        manifest_hash="test-hash-1",
    )
    return index_path


def _mock_repo(output_dir: str | None = None, missing: bool = False):
    index = MagicMock()
    index.output_dir = output_dir
    repo = MagicMock()
    repo.get_by_id.return_value = None if missing else index
    return repo


@pytest.fixture(autouse=True)
def _allow_test_legacy_graph_artifacts(monkeypatch):
    resolver = CodeCompassGraphArtifactResolver(
        artifact_root=None,
        allow_legacy=True,
    )
    monkeypatch.setattr(
        "agent.routes.codecompass_graph.get_codecompass_graph_artifact_resolver",
        lambda: resolver,
    )


# ── GET /api/codecompass/graph ────────────────────────────────────────────────

def test_get_graph_returns_domain_graph_artifact(client, auth_header, tmp_path):
    _build_graph_index(tmp_path)
    repo = _mock_repo(output_dir=str(tmp_path))
    with patch("agent.routes.codecompass_graph._knowledge_index_repo", return_value=repo):
        resp = client.get("/api/codecompass/graph?knowledge_index_id=idx-1", headers=auth_header)
    assert resp.status_code == 200
    data = resp.json["data"]
    assert data["schema"] == "domain_graph_artifact.v1"
    assert data["source_kind"] == "codecompass_graph"
    assert data["source_ref"] == "idx-1"


def test_get_graph_nodes_have_correct_structure(client, auth_header, tmp_path):
    _build_graph_index(tmp_path)
    repo = _mock_repo(output_dir=str(tmp_path))
    with patch("agent.routes.codecompass_graph._knowledge_index_repo", return_value=repo):
        resp = client.get("/api/codecompass/graph?knowledge_index_id=idx-1", headers=auth_header)
    nodes = resp.json["data"]["nodes"]
    assert len(nodes) == 3
    n1 = next(n for n in nodes if n["node_id"] == "n1")
    assert n1["node_type"] == "java_type"
    assert n1["attributes"]["name"] == "OrderService"
    assert n1["attributes"]["file"] == "src/OrderService.java"


def test_get_graph_edges_have_correct_structure(client, auth_header, tmp_path):
    _build_graph_index(tmp_path)
    repo = _mock_repo(output_dir=str(tmp_path))
    with patch("agent.routes.codecompass_graph._knowledge_index_repo", return_value=repo):
        resp = client.get("/api/codecompass/graph?knowledge_index_id=idx-1", headers=auth_header)
    edges = resp.json["data"]["edges"]
    assert len(edges) == 2
    assert any(e["relation"] == "child_of_type" for e in edges)
    assert all("source_id" in e and "target_id" in e for e in edges)


def test_get_graph_metadata_has_counts(client, auth_header, tmp_path):
    _build_graph_index(tmp_path)
    repo = _mock_repo(output_dir=str(tmp_path))
    with patch("agent.routes.codecompass_graph._knowledge_index_repo", return_value=repo):
        resp = client.get("/api/codecompass/graph?knowledge_index_id=idx-1", headers=auth_header)
    meta = resp.json["data"]["metadata"]
    assert meta["node_count"] == 3
    assert meta["edge_count"] == 2


def test_get_graph_projects_revision_capabilities_domains_and_worker_metrics(client, auth_header, tmp_path):
    _build_graph_index(tmp_path)
    repo = _mock_repo(output_dir=str(tmp_path))
    with patch("agent.routes.codecompass_graph._knowledge_index_repo", return_value=repo):
        resp = client.get("/api/codecompass/graph?knowledge_index_id=idx-1", headers=auth_header)
    data = resp.json["data"]

    assert data["metadata"]["graph_revision"] == "test-hash-1"
    assert data["metadata"]["visual_metrics_content_hash"].startswith("sha256:")
    assert data["metric_capabilities"]["in_degree"]["status"] == "available"
    n1 = next(node for node in data["nodes"] if node["node_id"] == "n1")
    assert n1["attributes"]["domain_id"] == "src"
    assert n1["attributes"]["domain_path"] == "src"
    assert n1["attributes"]["metrics"]["total_degree"] == 2
    schema = json.loads(Path("schemas/artifacts/domain_graph_artifact.v1.json").read_text(encoding="utf-8"))
    assert list(Draft202012Validator(schema).iter_errors(data)) == []


def test_get_graph_preserves_raw_types_zero_values_and_parallel_edges(client, auth_header, tmp_path):
    store = CodeCompassGraphStore(index_path=tmp_path / "cc_graph_index.json")
    store.rebuild_from_output_records(
        manifest_hash="raw-revision",
        records=[
            {"_provenance": {"output_kind": "graph_nodes"}, "id": "a", "kind": "Vendor::Node"},
            {"_provenance": {"output_kind": "graph_nodes"}, "id": "b", "kind": "python_file"},
            {
                "_provenance": {"output_kind": "graph_edges"},
                "source": "a", "target": "b", "type": "Vendor::Relation",
                "confidence": 0, "multiplicity": 0,
            },
            {
                "_provenance": {"output_kind": "graph_edges"},
                "source": "a", "target": "b", "type": "Vendor::Relation",
                "confidence": 1,
            },
        ],
    )
    repo = _mock_repo(output_dir=str(tmp_path))
    with patch("agent.routes.codecompass_graph._knowledge_index_repo", return_value=repo):
        resp = client.get("/api/codecompass/graph?knowledge_index_id=idx-raw", headers=auth_header)
    data = resp.json["data"]
    node = next(item for item in data["nodes"] if item["node_id"] == "a")
    first, second = data["edges"]

    assert node["attributes"]["raw_node_type"] == "Vendor::Node"
    assert node["attributes"]["semantic_status"] == "semantically_unknown"
    assert first["relation"] == "Vendor::Relation"
    assert first["attributes"]["raw_edge_type"] == "Vendor::Relation"
    assert first["attributes"]["confidence"] == 0
    assert first["attributes"]["multiplicity"] == 0
    assert first["attributes"]["parallel_count"] == 2
    assert first["edge_id"] != second["edge_id"]


def test_get_graph_request_path_does_not_compute_advanced_metrics(client, auth_header, tmp_path):
    _build_graph_index(tmp_path)
    repo = _mock_repo(output_dir=str(tmp_path))
    with (
        patch("agent.routes.codecompass_graph._knowledge_index_repo", return_value=repo),
        patch("worker.retrieval.codecompass_graph_metrics.compute_graph_metrics") as degree_spy,
        patch("worker.retrieval.codecompass_blast_radius.compute_blast_radius") as blast_spy,
    ):
        resp = client.get("/api/codecompass/graph?knowledge_index_id=idx-1", headers=auth_header)

    assert resp.status_code == 200
    degree_spy.assert_not_called()
    blast_spy.assert_not_called()


def test_get_graph_missing_knowledge_index_id_returns_400(client, auth_header):
    resp = client.get("/api/codecompass/graph", headers=auth_header)
    assert resp.status_code == 400


def test_get_graph_unknown_index_returns_404(client, auth_header):
    repo = _mock_repo(missing=True)
    with patch("agent.routes.codecompass_graph._knowledge_index_repo", return_value=repo):
        resp = client.get("/api/codecompass/graph?knowledge_index_id=nope", headers=auth_header)
    assert resp.status_code == 404


@pytest.mark.parametrize(
    ("projection_state", "expected_status"),
    [("pending", 404), ("projected", 200)],
)
def test_get_graph_requires_projected_v2_after_source_authorization(
    client,
    auth_header,
    tmp_path,
    projection_state,
    expected_status,
):
    _build_graph_index(tmp_path)
    index = SimpleNamespace(
        id="idx-v2",
        source_scope="repository",
        status="completed",
        output_dir=str(tmp_path),
        index_metadata={
            "source_control_scope": {
                "tenant_id": "tenant-a",
                "project_id": "project-a",
            },
            KNOWLEDGE_INDEX_EXECUTION_BINDING_METADATA_KEY: {
                "schema": KNOWLEDGE_INDEX_MATERIALIZATION_BINDING_SCHEMA,
                "projection_state": projection_state,
                "execution_job_schema": KNOWLEDGE_INDEX_EXECUTION_JOB_SCHEMA,
                "job_id": "knowledge-index-" + ("1" * 32),
                "knowledge_index_id": "idx-v2",
                "authority_binding_digest": "a" * 64,
                "assignment_id": "assignment-1",
            },
        },
    )
    repository = SimpleNamespace(
        get_by_id=lambda index_id: index if index_id == index.id else None
    )

    with patch(
        "agent.routes.codecompass_graph._knowledge_index_repo",
        return_value=repository,
    ):
        response = client.get(
            "/api/codecompass/graph?knowledge_index_id=idx-v2",
            headers=auth_header,
        )

    assert response.status_code == expected_status


def test_get_graph_no_output_dir_returns_404(client, auth_header):
    repo = _mock_repo(output_dir=None)
    with patch("agent.routes.codecompass_graph._knowledge_index_repo", return_value=repo):
        resp = client.get("/api/codecompass/graph?knowledge_index_id=idx-2", headers=auth_header)
    assert resp.status_code == 404


def test_get_graph_degraded_when_index_missing(client, auth_header, tmp_path):
    repo = _mock_repo(output_dir=str(tmp_path))  # no cc_graph_index.json in tmp_path
    with patch("agent.routes.codecompass_graph._knowledge_index_repo", return_value=repo):
        resp = client.get("/api/codecompass/graph?knowledge_index_id=idx-3", headers=auth_header)
    assert resp.status_code == 200
    data = resp.json["data"]
    assert data["diagnostics"]["status"] == "degraded"
    assert len(data["nodes"]) == 0


# ── GET /api/codecompass/graph/node/<node_id> ─────────────────────────────────

def test_get_node_returns_node_details(client, auth_header, tmp_path):
    _build_graph_index(tmp_path)
    repo = _mock_repo(output_dir=str(tmp_path))
    with patch("agent.routes.codecompass_graph._knowledge_index_repo", return_value=repo):
        resp = client.get("/api/codecompass/graph/node/n2?knowledge_index_id=idx-1", headers=auth_header)
    assert resp.status_code == 200
    data = resp.json["data"]
    assert data["node_id"] == "n2"
    assert data["node_type"] == "java_method"
    assert data["attributes"]["name"] == "placeOrder"


def test_get_node_unknown_id_returns_404(client, auth_header, tmp_path):
    _build_graph_index(tmp_path)
    repo = _mock_repo(output_dir=str(tmp_path))
    with patch("agent.routes.codecompass_graph._knowledge_index_repo", return_value=repo):
        resp = client.get("/api/codecompass/graph/node/does-not-exist?knowledge_index_id=idx-1", headers=auth_header)
    assert resp.status_code == 404


# ── GET /api/codecompass/graph/expand ─────────────────────────────────────────

def test_expand_graph_returns_traversal(client, auth_header, tmp_path):
    _build_graph_index(tmp_path)
    repo = _mock_repo(output_dir=str(tmp_path))
    with patch("agent.routes.codecompass_graph._knowledge_index_repo", return_value=repo):
        resp = client.get(
            "/api/codecompass/graph/expand?knowledge_index_id=idx-1&seed=n2&profile=bugfix_local",
            headers=auth_header,
        )
    assert resp.status_code == 200
    data = resp.json["data"]
    assert data["schema"] == "domain_graph_artifact.v1"
    assert data["source_kind"] == "codecompass_graph_expansion"
    assert data["source_ref"] == "idx-1:bugfix_local:n2"
    assert data["metadata"]["parent_graph_revision"] == "test-hash-1"
    assert data["metadata"]["graph_revision"] != "test-hash-1"
    assert len(data["nodes"]) >= 1
    node_ids = {n["node_id"] for n in data["nodes"]}
    assert "n2" in node_ids


def test_expand_graph_preserves_parallel_store_edges(client, auth_header, tmp_path):
    store = CodeCompassGraphStore(index_path=tmp_path / "cc_graph_index.json")
    store.rebuild_from_output_records(
        manifest_hash="parallel-expansion-revision",
        records=[
            {"_provenance": {"output_kind": "graph_nodes"}, "id": "a", "kind": "python_file"},
            {"_provenance": {"output_kind": "graph_nodes"}, "id": "b", "kind": "python_function"},
            {
                "_provenance": {"output_kind": "graph_edges"},
                "source": "a", "target": "b", "type": "calls_probable_target", "confidence": 0,
            },
            {
                "_provenance": {"output_kind": "graph_edges"},
                "source": "a", "target": "b", "type": "calls_probable_target", "confidence": 1,
            },
        ],
    )
    repo = _mock_repo(output_dir=str(tmp_path))
    with patch("agent.routes.codecompass_graph._knowledge_index_repo", return_value=repo):
        resp = client.get(
            "/api/codecompass/graph/expand?knowledge_index_id=idx-1&seed=a&profile=bugfix_local",
            headers=auth_header,
        )

    assert resp.status_code == 200
    edges = resp.json["data"]["edges"]
    assert len(edges) == 2
    assert {edge["attributes"]["confidence"] for edge in edges} == {0, 1}
    assert len({edge["edge_id"] for edge in edges}) == 2


def test_expand_graph_missing_seed_returns_400(client, auth_header, tmp_path):
    _build_graph_index(tmp_path)
    repo = _mock_repo(output_dir=str(tmp_path))
    with patch("agent.routes.codecompass_graph._knowledge_index_repo", return_value=repo):
        resp = client.get(
            "/api/codecompass/graph/expand?knowledge_index_id=idx-1&profile=bugfix_local",
            headers=auth_header,
        )
    assert resp.status_code == 400


def test_expand_graph_invalid_profile_returns_400(client, auth_header, tmp_path):
    _build_graph_index(tmp_path)
    repo = _mock_repo(output_dir=str(tmp_path))
    with patch("agent.routes.codecompass_graph._knowledge_index_repo", return_value=repo):
        resp = client.get(
            "/api/codecompass/graph/expand?knowledge_index_id=idx-1&seed=n1&profile=invalid_profile",
            headers=auth_header,
        )
    assert resp.status_code == 400


def test_expand_graph_default_profile_is_bugfix_local(client, auth_header, tmp_path):
    _build_graph_index(tmp_path)
    repo = _mock_repo(output_dir=str(tmp_path))
    with patch("agent.routes.codecompass_graph._knowledge_index_repo", return_value=repo):
        resp = client.get(
            "/api/codecompass/graph/expand?knowledge_index_id=idx-1&seed=n2",
            headers=auth_header,
        )
    assert resp.status_code == 200
    assert resp.json["data"]["metadata"]["profile"] == "bugfix_local"


# ── GET /api/codecompass/query (CCAQE-017) ────────────────────────────────────

def _build_architecture_index(tmp_path: Path) -> Path:
    fixture = json.loads(
        Path("tests/fixtures/codecompass_architecture/graph_records.json").read_text(encoding="utf-8")
    )
    index_path = tmp_path / "cc_graph_index.json"
    store = CodeCompassGraphStore(index_path=index_path)
    store.rebuild_from_output_records(records=fixture["records"], manifest_hash=fixture["manifest_hash"])
    return index_path


def test_architecture_query_dto_impact_happy_path(client, auth_header, tmp_path):
    _build_architecture_index(tmp_path)
    repo = _mock_repo(output_dir=str(tmp_path))
    with patch("agent.routes.codecompass_graph._knowledge_index_repo", return_value=repo):
        resp = client.get(
            "/api/codecompass/query?knowledge_index_id=idx-1&type=dto-impact&seed=UserDto",
            headers=auth_header,
        )
    assert resp.status_code == 200
    data = resp.json["data"]
    assert data["schema"] == "codecompass_architecture_query_result.v1"
    assert data["query_type"] == "dto-impact"
    assert data["results"]
    first = data["results"][0]
    assert first["evidence_paths"]
    assert data["metadata"]["knowledge_index_id"] == "idx-1"


def test_architecture_query_invalid_query_type_returns_400_with_valid_types(client, auth_header, tmp_path):
    _build_architecture_index(tmp_path)
    repo = _mock_repo(output_dir=str(tmp_path))
    with patch("agent.routes.codecompass_graph._knowledge_index_repo", return_value=repo):
        resp = client.get(
            "/api/codecompass/query?knowledge_index_id=idx-1&type=free-cypher&seed=UserDto",
            headers=auth_header,
        )
    assert resp.status_code == 400
    assert "dto-impact" in json.dumps(resp.json)


def test_architecture_query_missing_knowledge_index_id_returns_400(client, auth_header):
    resp = client.get("/api/codecompass/query?type=dto-impact&seed=UserDto", headers=auth_header)
    assert resp.status_code == 400


def test_architecture_query_missing_seed_returns_400(client, auth_header, tmp_path):
    _build_architecture_index(tmp_path)
    repo = _mock_repo(output_dir=str(tmp_path))
    with patch("agent.routes.codecompass_graph._knowledge_index_repo", return_value=repo):
        resp = client.get(
            "/api/codecompass/query?knowledge_index_id=idx-1&type=dto-impact",
            headers=auth_header,
        )
    assert resp.status_code == 400


def test_architecture_query_invalid_depth_and_direction_return_400(client, auth_header, tmp_path):
    _build_architecture_index(tmp_path)
    repo = _mock_repo(output_dir=str(tmp_path))
    with patch("agent.routes.codecompass_graph._knowledge_index_repo", return_value=repo):
        bad_depth = client.get(
            "/api/codecompass/query?knowledge_index_id=idx-1&type=dto-impact&seed=UserDto&depth=abc",
            headers=auth_header,
        )
        bad_direction = client.get(
            "/api/codecompass/query?knowledge_index_id=idx-1&type=dto-impact&seed=UserDto&direction=sideways",
            headers=auth_header,
        )
    assert bad_depth.status_code == 400
    assert bad_direction.status_code == 400


def test_architecture_query_unknown_seed_returns_valid_empty_result(client, auth_header, tmp_path):
    _build_architecture_index(tmp_path)
    repo = _mock_repo(output_dir=str(tmp_path))
    with patch("agent.routes.codecompass_graph._knowledge_index_repo", return_value=repo):
        resp = client.get(
            "/api/codecompass/query?knowledge_index_id=idx-1&type=dto-impact&seed=NotThere",
            headers=auth_header,
        )
    assert resp.status_code == 200
    data = resp.json["data"]
    assert data["results"] == []
    assert "seed_not_resolved" in data["warnings"]


# ── unauthenticated ───────────────────────────────────────────────────────────

def test_unauthenticated_graph_returns_401(client):
    resp = client.get("/api/codecompass/graph?knowledge_index_id=idx-1")
    assert resp.status_code == 401


def test_unauthenticated_query_returns_401(client):
    resp = client.get("/api/codecompass/query?knowledge_index_id=idx-1&type=dto-impact&seed=UserDto")
    assert resp.status_code == 401
