"""Actual MCP/graph-file/Task-tool admission under synthetic source fixtures."""

import json
from types import SimpleNamespace

import pytest

from agent.services.codecompass_scoped_graph import load_scoped_graph
from agent.services.codecompass_task_capability import current_task_capability, task_capability_scope
from agent.services.mcp_registry_service import get_mcp_registry_service
from tests.meet.test_meet_companion_task_grants import assigned as assigned
from tests.meet.test_meet_companion_task_tools import hub as hub
from tests.meet.test_meet_companion_task_tools import post

pytestmark = pytest.mark.timeout(15)


@pytest.fixture
def graphs(hub, tmp_path, monkeypatch):
    from agent.services import codecompass_graph_artifact_resolver, repository_registry

    admitted = tmp_path / "synthetic-allowed.json"
    foreign = tmp_path / "synthetic-foreign.json"
    payload = {"nodes": [
        {"id": "visible", "path": "worker/visible.py", "title": "Visible"},
        {"id": "visible2", "path": "worker/next.py", "title": "Visible next"},
        {"id": "sibling", "path": "worker-secret/private.py", "title": "Sibling secret"},
        {"id": "traversal", "path": "worker/../secret.py", "title": "Traversal secret"},
        {"id": "outside", "path": "secret/private.py", "title": "Outside"},
    ], "edges": [{"source": "visible", "target": "visible2"},
                 {"source": "visible", "target": "outside"}]}
    admitted.write_text(json.dumps(payload))
    foreign.write_text(json.dumps({"nodes": [{"id": "foreign", "path": "worker/visible.py",
                                             "title": "Foreign secret"}], "edges": []}))
    rows = [SimpleNamespace(id="foreign", status="completed", index_metadata={}),
            SimpleNamespace(id="index", status="completed", index_metadata={})]
    repository = SimpleNamespace(list_completed=lambda: rows,
                                 get_by_id=lambda identity: next((row for row in rows if row.id == identity), None))
    resolved = []

    class Artifacts:
        def resolve_artifacts(self, index):
            resolved.append(index.id)
            return (admitted if index.id == "index" else foreign), tmp_path / "unused-metrics.json"

    monkeypatch.setattr(repository_registry, "get_repository_registry",
                        lambda: SimpleNamespace(knowledge_index_repo=repository))
    monkeypatch.setattr(codecompass_graph_artifact_resolver, "get_codecompass_graph_artifact_resolver",
                        lambda: Artifacts())
    hub.capability["allowed_signals"] = ["graph"]
    hub.service.execution.registry = get_mcp_registry_service()
    return SimpleNamespace(**locals())


def test_actual_intelligence_handler_uses_only_current_index_and_paths(graphs):
    _, response = post(graphs.hub, graphs.hub.request | {"tool": "codecompass.architecture_intelligence",
                                                       "arguments": {}})
    assert response.status_code == 200, response.get_json()
    text = response.get_json()["text"]
    assert "Foreign secret" not in text and "worker-secret" not in text and "secret.py" not in text
    assert "visible" in text.lower() and graphs.resolved == ["index"]
    assert current_task_capability() is None


@pytest.mark.parametrize("arguments", [{"snapshot_ref": "foreign"}, {"revision": "2" * 40}])
def test_foreign_snapshot_or_revision_never_resolves_a_graph(graphs, arguments):
    _, response = post(graphs.hub, graphs.hub.request | {"tool": "codecompass.architecture_intelligence",
                                                       "arguments": arguments})
    assert response.status_code == 403 and graphs.resolved == []
    assert current_task_capability() is None


def test_exact_index_scope_excludes_legacy_rows_before_artifact_resolution(graphs):
    from agent.services.codecompass_graph_store_resolution_service import resolve_codecompass_graph_store

    with task_capability_scope({"allowed_index_ids": ["index"]}):
        store, identity, _diagnostics = resolve_codecompass_graph_store({})
    assert identity == "index" and graphs.resolved == ["index"]
    assert store.load()["nodes"][0]["id"] == "visible"
    graphs.resolved.clear()
    with task_capability_scope({"allowed_index_ids": []}):
        assert resolve_codecompass_graph_store({})[0] is None
    assert graphs.resolved == []


def test_scope_loader_removes_dangling_edges_and_rejects_unregistered_selection(graphs):
    from agent.services.codecompass_retrieval_capability_service import bind_retrieval_capability

    capability = bind_retrieval_capability(graphs.hub.capability, subject_id="ananta", tenant_id="tenant")
    graph, index, revision = load_scoped_graph(capability)
    assert index == "index" and revision == "1" * 40
    assert {node["id"] for node in graph["nodes"]} == {"visible", "visible2"}
    assert [(edge["source"], edge["target"]) for edge in graph["edges"]] == [("visible", "visible2")]
    capability["allowed_paths"] = ["secret/"]
    with pytest.raises(ValueError, match="digest_invalid"):
        load_scoped_graph(capability)


def test_existing_architecture_slice_cannot_treat_sibling_prefix_as_permission():
    from agent.services.codecompass_architecture_slice_service import CodeCompassArchitectureSliceService
    from tests.test_codecompass_hierarchical_architecture_context import _edges, _records

    # Use the existing service's actual permission filter, with an unrelated
    # sibling directory that used to match candidate.startswith(prefix).
    records, edges = _records(), _edges()
    records += [{"id": "private", "path": "agent-secret/private.py", "kind": "file"}]
    result = CodeCompassArchitectureSliceService().build_slice(
        query="private", records=records, edges=edges,
        capability={"allowed_paths": ["agent"], "revision": "1" * 40, "workspace_id": "workspace"})
    assert all(node["id"] != "private" for node in result["nodes"])
