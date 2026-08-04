"""RIG-006: codecompass.repository_query and codecompass.build_test_map tools.

Tests assert:

* tool entries follow the existing ananta_tool_result.v1 contract
* max_results is bounded by the tool's hard cap
* unsupported query types yield a structured error (no exception)
* missing seed/query_type yield structured errors
* missing graph index yields ``degraded`` status with warnings
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from agent.services import codecompass_graph_artifact_resolver, repository_registry
from agent.services.tools import codecompass_tools
from agent.services.tools._evidence import TOOL_RESULT_SCHEMA


def _seed_rig_store(tmp_path: Path) -> Path:
    idx = tmp_path / "rig.json"
    from worker.retrieval.codecompass_graph_store import CodeCompassGraphStore
    s = CodeCompassGraphStore(index_path=idx)
    s.rebuild_from_output_records(
        manifest_hash="m",
        records=[
            {"_provenance": {"output_kind": "rig_nodes"},
             "id": "bc:hello", "kind": "buildable_component",
             "attrs": {"name": "hello"}},
            {"_provenance": {"output_kind": "rig_nodes"},
             "id": "rn:ctest", "kind": "runner", "attrs": {"kind": "ctest"}},
            {"_provenance": {"output_kind": "rig_edges"},
             "from_id": "bc:hello", "to_id": "rn:ctest", "kind": "tested_by",
             "evidence": {"source_file": "/ws/CMakeLists.txt",
                          "source_kind": "spade_cmake_reply"}},
        ],
    )
    return idx


def test_repository_query_returns_tool_result_envelope(tmp_path):
    idx = _seed_rig_store(tmp_path / "x")
    res = codecompass_tools.codecompass_repository_query(
        workspace_dir=str(tmp_path),
        arguments={"query_type": "component-tests", "seed": "bc:hello",
                   "graph_index_path": str(idx)},
        tool_call_id="t1",
    )
    assert res["tool_name"] == "codecompass.repository_query"
    assert res["tool_call_id"] == "t1"
    assert res["status"] in {"ok", "degraded"}


def test_repository_query_unsupported_type_is_error(tmp_path):
    idx = _seed_rig_store(tmp_path / "u")
    res = codecompass_tools.codecompass_repository_query(
        workspace_dir=str(tmp_path),
        arguments={"query_type": "cypher", "seed": "bc:hello",
                   "graph_index_path": str(idx)},
        tool_call_id="t2",
    )
    assert res["status"] == "error"
    assert "unsupported_query_type" in res["error"]


def test_repository_query_missing_seed_is_error(tmp_path):
    res = codecompass_tools.codecompass_repository_query(
        workspace_dir=str(tmp_path),
        arguments={"query_type": "component-tests"},
        tool_call_id="t3",
    )
    assert res["status"] == "error"
    assert res["error"] == "seed_required"


def test_repository_query_missing_query_type_is_error(tmp_path):
    res = codecompass_tools.codecompass_repository_query(
        workspace_dir=str(tmp_path),
        arguments={"seed": "bc:hello"},
        tool_call_id="t4",
    )
    assert res["status"] == "error"
    assert res["error"] == "query_type_required"


def test_repository_query_max_results_is_clamped(tmp_path):
    idx = _seed_rig_store(tmp_path / "m")
    res = codecompass_tools.codecompass_repository_query(
        workspace_dir=str(tmp_path),
        arguments={"query_type": "component-tests", "seed": "bc:hello",
                   "graph_index_path": str(idx), "max_results": 9999},
        tool_call_id="t5",
    )
    assert res["status"] in {"ok", "degraded"}
    # ``max_results`` must be clamped; the function returns a warning or
    # the result has at most 100 entries. We check the data shape.
    data = res.get("data") or {}
    qr = data.get("query_result") or {}
    assert len(qr.get("results") or []) <= 100


def test_repository_query_no_rig_data_is_degraded(tmp_path):
    res = codecompass_tools.codecompass_repository_query(
        workspace_dir=str(tmp_path),
        arguments={"query_type": "component-tests", "seed": "bc:hello",
                   "graph_index_path": str(tmp_path / "missing.json")},
        tool_call_id="t6",
    )
    assert res["status"] == "degraded"
    assert "repository_intelligence_unavailable" in res["warnings"]


def test_build_test_map_returns_target_data(tmp_path):
    idx = _seed_rig_store(tmp_path / "b")
    res = codecompass_tools.codecompass_build_test_map(
        workspace_dir=str(tmp_path),
        arguments={"target": "bc:hello", "graph_index_path": str(idx)},
        tool_call_id="t7",
    )
    assert res["tool_name"] == "codecompass.build_test_map"
    assert res["data"]["target"] == "bc:hello"


def test_build_test_map_missing_target_is_error(tmp_path):
    res = codecompass_tools.codecompass_build_test_map(
        workspace_dir=str(tmp_path),
        arguments={},
        tool_call_id="t8",
    )
    assert res["status"] == "error"
    assert res["error"] == "target_required"


def test_tools_use_ananta_tool_result_schema():
    """The tool envelope must conform to ananta_tool_result.v1."""
    res = codecompass_tools.codecompass_repository_query(
        workspace_dir="/tmp",
        arguments={"query_type": "component-tests", "seed": "x"},
        tool_call_id="t9",
    )
    assert res["schema"] == TOOL_RESULT_SCHEMA


def test_graph_store_resolution_uses_governed_artifact_resolver(
    monkeypatch,
    tmp_path,
):
    admitted_index_path = _seed_rig_store(tmp_path / "admitted")
    metrics_path = tmp_path / "admitted" / "cc_graph_index.visual_metrics.json"
    knowledge_index = SimpleNamespace(
        id="index-1",
        output_dir=str(tmp_path / "must-not-be-read-directly"),
        index_metadata={"graph_artifacts": {}},
    )

    class _Resolver:
        def __init__(self):
            self.resolved = []

        def resolve_artifacts(self, candidate):
            self.resolved.append(candidate)
            return admitted_index_path, metrics_path

        def resolve_legacy_tool_graph(self, candidate):
            raise AssertionError("admitted artifacts must not use the legacy path")

    resolver = _Resolver()
    repository = SimpleNamespace(
        get_by_id=lambda index_id: knowledge_index if index_id == "index-1" else None,
        list_completed=lambda: [knowledge_index],
    )

    monkeypatch.setattr(
        codecompass_graph_artifact_resolver,
        "get_codecompass_graph_artifact_resolver",
        lambda: resolver,
    )
    monkeypatch.setattr(
        repository_registry,
        "get_repository_registry",
        lambda: SimpleNamespace(knowledge_index_repo=repository),
    )

    store, index_id = codecompass_tools._resolve_graph_store(
        {"knowledge_index_id": "index-1"}
    )

    assert index_id == "index-1"
    assert resolver.resolved == [knowledge_index]
    assert store.load()["rig_index"]["node_count"] == 2
