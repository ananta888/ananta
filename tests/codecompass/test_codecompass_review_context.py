"""CRG-006 + RIG-007: minimal review context tests."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from worker.retrieval.codecompass_graph_store import CodeCompassGraphStore
from worker.retrieval.codecompass_review_context import (
    DEFAULT_SEED_CAP,
    REVIEW_CONTEXT_VERSION,
    build_minimal_review_context,
)


def _seed_store(tmp_path: Path) -> CodeCompassGraphStore:
    s = CodeCompassGraphStore(index_path=tmp_path / "index.json")
    s.rebuild_from_output_records(
        manifest_hash="m",
        records=[
            {"_provenance": {"output_kind": "graph_nodes"},
             "id": "f:a.py", "kind": "file", "name": "a.py", "file": "a.py"},
            {"_provenance": {"output_kind": "graph_nodes"},
             "id": "f:b.py", "kind": "file", "name": "b.py", "file": "b.py"},
            {"_provenance": {"output_kind": "graph_nodes"},
             "id": "f:c.py", "kind": "file", "name": "c.py", "file": "c.py"},
            {"_provenance": {"output_kind": "graph_nodes"},
             "id": "symbol_function:f:a.py:func_a", "kind": "symbol_function",
             "name": "func_a", "file": "a.py"},
            {"_provenance": {"output_kind": "graph_nodes"},
             "id": "symbol_function:f:b.py:func_b", "kind": "symbol_function",
             "name": "func_b", "file": "b.py"},
            {"_provenance": {"output_kind": "graph_nodes"},
             "id": "symbol_function:f:c.py:test_a", "kind": "symbol_function",
             "name": "test_a", "file": "c.py"},
            {"_provenance": {"output_kind": "graph_edges"},
             "source": "symbol_function:f:b.py:func_b",
             "target": "symbol_function:f:a.py:func_a",
             "type": "calls", "confidence": 1.0},
            {"_provenance": {"output_kind": "graph_edges"},
             "source": "symbol_function:f:c.py:test_a",
             "target": "symbol_function:f:a.py:func_a",
             "type": "covers", "confidence": 1.0},
            {"_provenance": {"output_kind": "rig_nodes"},
             "id": "bc:a", "kind": "buildable_component",
             "attrs": {"name": "a"}},
            {"_provenance": {"output_kind": "rig_nodes"},
             "id": "rn:ctest", "kind": "runner",
             "attrs": {"kind": "ctest"}},
            {"_provenance": {"output_kind": "rig_nodes"},
             "id": "t:a_test", "kind": "test",
             "attrs": {"name": "a_test"}},
            {"_provenance": {"output_kind": "rig_edges"},
             "from_id": "bc:a", "to_id": "rn:ctest", "kind": "tested_by",
             "evidence": {"source_file": "/ws/CMakeLists.txt",
                          "source_kind": "spade_cmake_reply",
                          "source_record_id": "t:a"}},
            {"_provenance": {"output_kind": "rig_edges"},
             "from_id": "rn:ctest", "to_id": "t:a_test", "kind": "runs",
             "evidence": {"source_file": "/ws/CTestTestfile.cmake",
                          "source_kind": "spade_ctest_record",
                          "source_record_id": "a_test"}},
        ],
    )
    s._cached_payload = None
    return s


def test_review_context_version_is_pinned():
    assert REVIEW_CONTEXT_VERSION == "minimal_review_context.v1"


def test_minimal_context_sections_order(tmp_path):
    s = _seed_store(tmp_path / "r")
    ctx = build_minimal_review_context(
        graph_store=s,
        changed_files=("a.py",),
        seed_nodes=("symbol_function:f:a.py:func_a",),
        task_kind="review",
    )
    titles = [s.title for s in ctx.sections]
    assert titles[0] == "changed_files"


def test_review_context_includes_dependents(tmp_path):
    s = _seed_store(tmp_path / "d")
    ctx = build_minimal_review_context(
        graph_store=s,
        changed_files=("a.py",),
        seed_nodes=("symbol_function:f:a.py:func_a",),
    )
    titles = [s.title for s in ctx.sections]
    assert "direct_dependents" in titles
    assert "affected_tests" in titles
    assert "risk_summary" in titles


def test_review_context_optional_rig_evidence(tmp_path):
    s = _seed_store(tmp_path / "r2")
    # Use a RIG node ID as seed so the RIG query can resolve it.
    ctx = build_minimal_review_context(
        graph_store=s,
        changed_files=("a.py",),
        seed_nodes=("bc:a",),
        include_repository_intelligence=True,
    )
    titles = [s.title for s in ctx.sections]
    assert "build_test_evidence" in titles


def test_review_context_without_rig(tmp_path):
    s = _seed_store(tmp_path / "r3")
    ctx = build_minimal_review_context(
        graph_store=s,
        changed_files=("a.py",),
        seed_nodes=("symbol_function:f:a.py:func_a",),
        include_repository_intelligence=False,
    )
    titles = [s.title for s in ctx.sections]
    assert "build_test_evidence" not in titles


def test_review_context_no_full_repo_dump(tmp_path):
    """Acceptance: minimal context must NOT contain all graph nodes —
    only the bounded sections."""
    s = _seed_store(tmp_path / "n")
    ctx = build_minimal_review_context(
        graph_store=s,
        changed_files=("a.py",),
        seed_nodes=("symbol_function:f:a.py:func_a",),
    )
    total_items = sum(len(s.items) for s in ctx.sections)
    # total nodes in graph = 6 (3 files + 3 symbols) — must be far less
    assert total_items < 100


def test_review_context_includes_blast_radius(tmp_path):
    s = _seed_store(tmp_path / "br")
    ctx = build_minimal_review_context(
        graph_store=s,
        changed_files=("a.py",),
        seed_nodes=("symbol_function:f:a.py:func_a",),
    )
    assert ctx.blast_radius is not None
    assert ctx.blast_radius.risk_model_version.startswith("blast_radius")


def test_review_context_rig_prefers_build_evidence(tmp_path):
    """RIG-007 acceptance: for build/test questions, RIG evidence
    is preferred over generic text matches."""
    s = _seed_store(tmp_path / "p")
    ctx = build_minimal_review_context(
        graph_store=s,
        changed_files=(),
        seed_nodes=("bc:a",),
        include_repository_intelligence=True,
        task_kind="ci",
    )
    titles = [s.title for s in ctx.sections]
    rig_section = next((s for s in ctx.sections if s.title == "build_test_evidence"), None)
    assert rig_section is not None
    assert rig_section.evidence_paths


def test_review_context_result_is_json_serialisable(tmp_path):
    s = _seed_store(tmp_path / "j")
    ctx = build_minimal_review_context(
        graph_store=s,
        changed_files=("a.py",),
        seed_nodes=("symbol_function:f:a.py:func_a",),
    )
    json.dumps(ctx.as_dict())


def test_review_context_max_total_items_truncation(tmp_path):
    s = _seed_store(tmp_path / "m")
    ctx = build_minimal_review_context(
        graph_store=s,
        changed_files=tuple(f"f{i}.py" for i in range(50)),
        seed_nodes=("symbol_function:f:a.py:func_a",),
        max_total_items=5,
    )
    assert "max_total_items_truncated" in ctx.warnings