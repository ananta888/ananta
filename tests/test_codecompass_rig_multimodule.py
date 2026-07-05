"""RIG-010: cross-language / monorepo scope tests."""
from __future__ import annotations

from pathlib import Path

import pytest

from worker.retrieval.codecompass_graph_store import CodeCompassGraphStore
from worker.retrieval.codecompass_repository_intelligence_query import run_query


def _build_multiscope_store(tmp_path: Path) -> CodeCompassGraphStore:
    """Two modules sharing an external package."""
    s = CodeCompassGraphStore(index_path=tmp_path / "i.json")
    s.rebuild_from_output_records(
        manifest_hash="m",
        records=[
            # module A
            {"_provenance": {"output_kind": "rig_nodes"},
             "id": "bc:a1", "kind": "buildable_component",
             "attrs": {"name": "a1", "module_id": "modA",
                       "repository_id": "monorepo"}},
            {"_provenance": {"output_kind": "rig_nodes"},
             "id": "bc:a2", "kind": "buildable_component",
             "attrs": {"name": "a2", "module_id": "modA",
                       "repository_id": "monorepo"}},
            # module B
            {"_provenance": {"output_kind": "rig_nodes"},
             "id": "bc:b1", "kind": "buildable_component",
             "attrs": {"name": "b1", "module_id": "modB",
                       "repository_id": "monorepo"}},
            # shared external package
            {"_provenance": {"output_kind": "rig_nodes"},
             "id": "ep:fmt", "kind": "external_package",
             "attrs": {"name": "fmt"}},
            {"_provenance": {"output_kind": "rig_edges"},
             "from_id": "bc:a1", "to_id": "ep:fmt", "kind": "depends_on",
             "evidence": {"source_file": "/modA/CMakeLists.txt",
                          "source_kind": "spade_cmake_reply",
                          "source_record_id": "t:a1"}},
            {"_provenance": {"output_kind": "rig_edges"},
             "from_id": "bc:b1", "to_id": "ep:fmt", "kind": "depends_on",
             "evidence": {"source_file": "/modB/CMakeLists.txt",
                          "source_kind": "spade_cmake_reply",
                          "source_record_id": "t:b1"}},
        ],
    )
    s._cached_payload = None
    return s


def test_cross_scope_returns_all_modules(tmp_path):
    s = _build_multiscope_store(tmp_path / "cs")
    res = run_query(graph_store=s, query_type="package-dependents",
                    seed="ep:fmt", cross_scope=True)
    components = {r.get("from") for r in res.results}
    assert {"bc:a1", "bc:b1"}.issubset(components)


def test_scope_filter_limits_to_one_module(tmp_path):
    s = _build_multiscope_store(tmp_path / "sc")
    res = run_query(graph_store=s, query_type="package-dependents",
                    seed="ep:fmt", module_id="modA")
    components = {r.get("from") for r in res.results}
    assert components == {"bc:a1"}
    # Evidence per module is preserved (RIG-010 acceptance).
    assert any("modA" in p for p in res.evidence_paths)
    assert not any("modB" in p for p in res.evidence_paths)


def test_repository_scope(tmp_path):
    s = _build_multiscope_store(tmp_path / "rep")
    res = run_query(graph_store=s, query_type="package-dependents",
                    seed="ep:fmt", repository_id="monorepo",
                    cross_scope=True)
    # repository_id filter accepts only nodes that have the field set
    # AND match — but our fixture sets it on bc:* nodes, not on ep:fmt.
    # We expect bc:a1 and bc:b1 since their repo matches.
    components = {r.get("from") for r in res.results}
    assert components == {"bc:a1", "bc:b1"}


def test_external_package_dedup_across_modules(tmp_path):
    s = _build_multiscope_store(tmp_path / "ed")
    res = run_query(graph_store=s, query_type="external-package-impact",
                    seed="ep:fmt", cross_scope=True)
    # Despite two dependents (bc:a1, bc:b1), the external-package
    # query deduplicates by package. We expect ONE result (since the
    # deduplication is on the package, not the component list).
    assert len(res.results) == 1


def test_seed_resolution_records_scope(tmp_path):
    s = _build_multiscope_store(tmp_path / "sr")
    res = run_query(graph_store=s, query_type="package-dependents",
                    seed="ep:fmt", module_id="modA")
    assert res.seed_resolution["scope"]["module_id"] == "modA"
    assert res.seed_resolution["scope"]["cross_scope"] is False


def test_unknown_module_returns_no_results(tmp_path):
    s = _build_multiscope_store(tmp_path / "um")
    res = run_query(graph_store=s, query_type="package-dependents",
                    seed="ep:fmt", module_id="does_not_exist")
    # bc:a1 and bc:b1 do not have module_id=does_not_exist, so filter
    # rejects them -> no results.
    assert res.results == ()


def test_two_modules_share_dependency_different_tests(tmp_path):
    """Acceptance: two modules with a shared dependency and different
    tests must both show their own evidence."""
    s = CodeCompassGraphStore(index_path=tmp_path / "i2.json")
    s.rebuild_from_output_records(
        manifest_hash="m",
        records=[
            {"_provenance": {"output_kind": "rig_nodes"},
             "id": "bc:a1", "kind": "buildable_component",
             "attrs": {"name": "a1", "module_id": "modA"}},
            {"_provenance": {"output_kind": "rig_nodes"},
             "id": "bc:b1", "kind": "buildable_component",
             "attrs": {"name": "b1", "module_id": "modB"}},
            {"_provenance": {"output_kind": "rig_nodes"},
             "id": "ep:fmt", "kind": "external_package",
             "attrs": {"name": "fmt"}},
            {"_provenance": {"output_kind": "rig_nodes"},
             "id": "rn:ctestA", "kind": "runner",
             "attrs": {"kind": "ctest", "module_id": "modA"}},
            {"_provenance": {"output_kind": "rig_nodes"},
             "id": "rn:ctestB", "kind": "runner",
             "attrs": {"kind": "ctest", "module_id": "modB"}},
            {"_provenance": {"output_kind": "rig_nodes"},
             "id": "t:a1_test", "kind": "test",
             "attrs": {"name": "a1_test", "module_id": "modA"}},
            {"_provenance": {"output_kind": "rig_nodes"},
             "id": "t:b1_test", "kind": "test",
             "attrs": {"name": "b1_test", "module_id": "modB"}},
            {"_provenance": {"output_kind": "rig_edges"},
             "from_id": "bc:a1", "to_id": "ep:fmt", "kind": "depends_on",
             "evidence": {"source_file": "/modA/CMakeLists.txt",
                          "source_kind": "spade_cmake_reply"}},
            {"_provenance": {"output_kind": "rig_edges"},
             "from_id": "bc:a1", "to_id": "rn:ctestA", "kind": "tested_by",
             "evidence": {"source_file": "/modA/CMakeLists.txt",
                          "source_kind": "spade_cmake_reply"}},
            {"_provenance": {"output_kind": "rig_edges"},
             "from_id": "rn:ctestA", "to_id": "t:a1_test", "kind": "runs",
             "evidence": {"source_file": "/modA/CTestTestfile.cmake",
                          "source_kind": "spade_ctest_record",
                          "source_record_id": "a1_test"}},
            {"_provenance": {"output_kind": "rig_edges"},
             "from_id": "bc:b1", "to_id": "ep:fmt", "kind": "depends_on",
             "evidence": {"source_file": "/modB/CMakeLists.txt",
                          "source_kind": "spade_cmake_reply"}},
            {"_provenance": {"output_kind": "rig_edges"},
             "from_id": "bc:b1", "to_id": "rn:ctestB", "kind": "tested_by",
             "evidence": {"source_file": "/modB/CMakeLists.txt",
                          "source_kind": "spade_cmake_reply"}},
            {"_provenance": {"output_kind": "rig_edges"},
             "from_id": "rn:ctestB", "to_id": "t:b1_test", "kind": "runs",
             "evidence": {"source_file": "/modB/CTestTestfile.cmake",
                          "source_kind": "spade_ctest_record",
                          "source_record_id": "b1_test"}},
        ],
    )
    s._cached_payload = None
    res_A = run_query(graph_store=s, query_type="component-tests",
                      seed="bc:a1", module_id="modA")
    res_B = run_query(graph_store=s, query_type="component-tests",
                      seed="bc:b1", module_id="modB")
    assert any("a1_test" in str(r) for r in res_A.results)
    assert any("b1_test" in str(r) for r in res_B.results)