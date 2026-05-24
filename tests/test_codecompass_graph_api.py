from __future__ import annotations

import json
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

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


# ── GET /api/codecompass/graph ────────────────────────────────────────────────

def test_get_graph_returns_domain_graph_artifact(client, auth_header, tmp_path):
    index_path = _build_graph_index(tmp_path)
    repo = _mock_repo(output_dir=str(tmp_path))
    with patch("agent.routes.codecompass_graph._knowledge_index_repo", return_value=repo):
        resp = client.get("/api/codecompass/graph?knowledge_index_id=idx-1", headers=auth_header)
    assert resp.status_code == 200
    data = resp.json["data"]
    assert data["schema"] == "domain_graph_artifact.v1"
    assert data["source_kind"] == "codecompass_graph"
    assert data["source_ref"] == "idx-1"


def test_get_graph_nodes_have_correct_structure(client, auth_header, tmp_path):
    index_path = _build_graph_index(tmp_path)
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
    index_path = _build_graph_index(tmp_path)
    repo = _mock_repo(output_dir=str(tmp_path))
    with patch("agent.routes.codecompass_graph._knowledge_index_repo", return_value=repo):
        resp = client.get("/api/codecompass/graph?knowledge_index_id=idx-1", headers=auth_header)
    edges = resp.json["data"]["edges"]
    assert len(edges) == 2
    assert any(e["relation"] == "child_of_type" for e in edges)
    assert all("source_id" in e and "target_id" in e for e in edges)


def test_get_graph_metadata_has_counts(client, auth_header, tmp_path):
    index_path = _build_graph_index(tmp_path)
    repo = _mock_repo(output_dir=str(tmp_path))
    with patch("agent.routes.codecompass_graph._knowledge_index_repo", return_value=repo):
        resp = client.get("/api/codecompass/graph?knowledge_index_id=idx-1", headers=auth_header)
    meta = resp.json["data"]["metadata"]
    assert meta["node_count"] == 3
    assert meta["edge_count"] == 2


def test_get_graph_missing_knowledge_index_id_returns_400(client, auth_header):
    resp = client.get("/api/codecompass/graph", headers=auth_header)
    assert resp.status_code == 400


def test_get_graph_unknown_index_returns_404(client, auth_header):
    repo = _mock_repo(missing=True)
    with patch("agent.routes.codecompass_graph._knowledge_index_repo", return_value=repo):
        resp = client.get("/api/codecompass/graph?knowledge_index_id=nope", headers=auth_header)
    assert resp.status_code == 404


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
    assert len(data["nodes"]) >= 1
    node_ids = {n["node_id"] for n in data["nodes"]}
    assert "n2" in node_ids


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


# ── unauthenticated ───────────────────────────────────────────────────────────

def test_unauthenticated_graph_returns_401(client):
    resp = client.get("/api/codecompass/graph?knowledge_index_id=idx-1")
    assert resp.status_code == 401
