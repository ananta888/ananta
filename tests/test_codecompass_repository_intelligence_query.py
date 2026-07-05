"""RIG-005: Repository Intelligence Query tests."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from worker.retrieval.codecompass_graph_store import CodeCompassGraphStore
from worker.retrieval.codecompass_repository_intelligence_query import (
    ALLOWED_QUERY_TYPES,
    QUERY_ENGINE_VERSION,
    run_query,
)


def _seed_rig_store(tmp_path: Path) -> CodeCompassGraphStore:
    s = CodeCompassGraphStore(index_path=tmp_path / "index.json")
    s.rebuild_from_output_records(
        manifest_hash="m1",
        records=[
            {"_provenance": {"output_kind": "rig_nodes"},
             "id": "bc:hello", "kind": "buildable_component",
             "attrs": {"name": "hello", "source_files": ["src/hello.cpp"]}},
            {"_provenance": {"output_kind": "rig_nodes"},
             "id": "ep:fmt", "kind": "external_package",
             "attrs": {"name": "fmt"}},
            {"_provenance": {"output_kind": "rig_nodes"},
             "id": "rn:ctest", "kind": "runner",
             "attrs": {"kind": "ctest"}},
            {"_provenance": {"output_kind": "rig_nodes"},
             "id": "t:hello_test", "kind": "test",
             "attrs": {"name": "hello_test"}},
            {"_provenance": {"output_kind": "rig_edges"},
             "from_id": "bc:hello", "to_id": "ep:fmt", "kind": "depends_on",
             "evidence": {"source_file": "/ws/CMakeLists.txt",
                          "source_kind": "spade_cmake_reply",
                          "source_record_id": "target:hello"}},
            {"_provenance": {"output_kind": "rig_edges"},
             "from_id": "bc:hello", "to_id": "rn:ctest", "kind": "tested_by",
             "evidence": {"source_file": "/ws/CMakeLists.txt",
                          "source_kind": "spade_cmake_reply"}},
            {"_provenance": {"output_kind": "rig_edges"},
             "from_id": "rn:ctest", "to_id": "t:hello_test", "kind": "runs",
             "evidence": {"source_file": "/ws/CTestTestfile.cmake",
                          "source_kind": "spade_ctest_record",
                          "source_record_id": "hello_test"}},
        ],
    )
    s._cached_payload = None
    return s


def test_query_engine_version_is_pinned():
    assert QUERY_ENGINE_VERSION == "repository_intelligence_query.v1"


def test_allowed_query_types_are_frozen():
    assert "free-form-query" not in ALLOWED_QUERY_TYPES
    assert "component-tests" in ALLOWED_QUERY_TYPES


def test_unknown_query_type_raises():
    s = _seed_rig_store(Path("/tmp/_q_1"))
    with pytest.raises(ValueError, match="unsupported query_type"):
        run_query(graph_store=s, query_type="cypher", seed="x")


def test_component_tests_query(tmp_path):
    s = _seed_rig_store(tmp_path / "q")
    res = run_query(graph_store=s, query_type="component-tests", seed="bc:hello")
    assert res.query_type == "component-tests"
    # tested_by + runs together cover the path to the test
    kinds = {r["edge_kind"] for r in res.results}
    assert "tested_by" in kinds


def test_package_dependents_query(tmp_path):
    s = _seed_rig_store(tmp_path / "p")
    res = run_query(graph_store=s, query_type="package-dependents", seed="ep:fmt")
    assert any(r.get("from") == "bc:hello" and r.get("edge_kind") == "depends_on"
               for r in res.results)


def test_runner_coverage_query(tmp_path):
    s = _seed_rig_store(tmp_path / "r")
    res = run_query(graph_store=s, query_type="runner-coverage", seed="rn:ctest")
    assert any(r.get("test") == "t:hello_test" for r in res.results)


def test_build_target_chain_query(tmp_path):
    s = _seed_rig_store(tmp_path / "b")
    res = run_query(graph_store=s, query_type="build-target-chain", seed="bc:hello")
    # no built_by edges in fixture -> empty results, but seed resolution works
    assert res.seed_resolution["matched_node_ids"] == ["bc:hello"]


def test_external_package_impact_query(tmp_path):
    s = _seed_rig_store(tmp_path / "i")
    res = run_query(graph_store=s, query_type="external-package-impact", seed="ep:fmt")
    assert any(r.get("component") == "bc:hello" and r.get("package") == "ep:fmt"
               for r in res.results)


def test_seed_resolution_by_name(tmp_path):
    s = _seed_rig_store(tmp_path / "n")
    res = run_query(graph_store=s, query_type="component-tests", seed="hello")
    assert res.seed_resolution["matched_via"] == "name"
    assert "bc:hello" in res.seed_resolution["matched_node_ids"]


def test_seed_not_found(tmp_path):
    s = _seed_rig_store(tmp_path / "nf")
    res = run_query(graph_store=s, query_type="component-tests", seed="does_not_exist")
    assert "seed_not_found" in res.warnings
    assert res.results == ()


def test_no_rig_data_warns(tmp_path):
    s = CodeCompassGraphStore(index_path=tmp_path / "e.json")
    res = run_query(graph_store=s, query_type="component-tests", seed="anything")
    assert "repository_intelligence_unavailable" in res.warnings
    assert res.confidence == 0.0


def test_evidence_paths_present(tmp_path):
    s = _seed_rig_store(tmp_path / "ev")
    res = run_query(graph_store=s, query_type="external-package-impact", seed="ep:fmt")
    assert res.evidence_paths
    assert any("CMakeLists" in p for p in res.evidence_paths)


def test_query_result_is_json_serialisable(tmp_path):
    s = _seed_rig_store(tmp_path / "j")
    res = run_query(graph_store=s, query_type="component-tests", seed="bc:hello")
    json.dumps(res.as_dict())