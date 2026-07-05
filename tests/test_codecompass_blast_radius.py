"""CRG-005: blast-radius query tests."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from worker.retrieval.codecompass_blast_radius import (
    RISK_MODEL_VERSION,
    BlastRadiusResult,
    compute_blast_radius,
)
from worker.retrieval.codecompass_graph_store import CodeCompassGraphStore


def _seed_store(tmp_path: Path) -> CodeCompassGraphStore:
    """Build a small graph: file a.py contains func_a (called by func_b in file b.py);
    file c.py contains test_a (covers func_a)."""
    s = CodeCompassGraphStore(index_path=tmp_path / "index.json")
    s.rebuild_from_output_records(
        manifest_hash="m1",
        records=[
            {"_provenance": {"output_kind": "graph_nodes"}, "id": "file:a.py", "kind": "file", "name": "a.py", "file": "a.py"},
            {"_provenance": {"output_kind": "graph_nodes"}, "id": "file:b.py", "kind": "file", "name": "b.py", "file": "b.py"},
            {"_provenance": {"output_kind": "graph_nodes"}, "id": "file:c.py", "kind": "file", "name": "c.py", "file": "c.py"},
            {"_provenance": {"output_kind": "graph_nodes"}, "id": "symbol_function:file:a.py:func_a", "kind": "symbol_function", "name": "func_a", "file": "a.py"},
            {"_provenance": {"output_kind": "graph_nodes"}, "id": "symbol_function:file:b.py:func_b", "kind": "symbol_function", "name": "func_b", "file": "b.py"},
            {"_provenance": {"output_kind": "graph_nodes"}, "id": "symbol_function:file:c.py:test_a", "kind": "symbol_function", "name": "test_a", "file": "c.py"},
            {"_provenance": {"output_kind": "graph_edges"}, "source": "symbol_function:file:b.py:func_b", "target": "symbol_function:file:a.py:func_a", "type": "calls", "confidence": 1.0},
            {"_provenance": {"output_kind": "graph_edges"}, "source": "symbol_function:file:c.py:test_a", "target": "symbol_function:file:a.py:func_a", "type": "covers", "confidence": 1.0},
        ],
    )
    s._cached_payload = None
    return s


def test_blast_radius_finds_caller_and_test():
    store = _seed_store(Path("/tmp/_br_test_1"))
    res = compute_blast_radius(
        graph_store=store,
        seed_nodes=("symbol_function:file:a.py:func_a",),
        max_depth=3,
    )
    assert "b.py" in res.affected_files
    assert "c.py" in res.affected_files
    assert "symbol_function:file:b.py:func_b" in res.affected_symbols
    assert res.risk_model_version == RISK_MODEL_VERSION
    assert 0.0 <= res.risk_score <= 1.0


def test_blast_radius_without_tests_lowers_risk_score():
    store = _seed_store(Path("/tmp/_br_test_2"))
    res_with = compute_blast_radius(
        graph_store=store,
        seed_nodes=("symbol_function:file:a.py:func_a",),
        max_depth=3,
        include_tests=True,
    )
    res_without = compute_blast_radius(
        graph_store=store,
        seed_nodes=("symbol_function:file:a.py:func_a",),
        max_depth=3,
        include_tests=False,
    )
    # Without tests, the inverse-test component becomes 1.0 and the
    # risk score goes up.
    assert res_without.risk_score >= res_with.risk_score


def test_blast_radius_max_depth_zero_returns_seeds_only(tmp_path):
    store = _seed_store(tmp_path / "x")
    res = compute_blast_radius(
        graph_store=store,
        seed_nodes=("symbol_function:file:a.py:func_a",),
        max_depth=0,
    )
    assert "max_depth_zero_no_expansion" in res.warnings


def test_blast_risk_model_version_is_pinned():
    assert RISK_MODEL_VERSION == "blast_radius.v1"


def test_blast_radius_warns_when_node_cap_reached(tmp_path):
    """A large node cap must be respected and the result must carry a
    node_cap_reached warning when we hit it."""
    # Build a graph with a single root and many incoming edges.
    s = CodeCompassGraphStore(index_path=tmp_path / "index.json")
    records = [
        {"_provenance": {"output_kind": "graph_nodes"}, "id": "f:root", "kind": "file", "name": "root", "file": "root.py"},
    ]
    for i in range(60):
        records.append({
            "_provenance": {"output_kind": "graph_nodes"},
            "id": f"symbol_function:src{i}.py:fn{i}",
            "kind": "symbol_function", "name": f"fn{i}", "file": f"src{i}.py",
        })
        records.append({
            "_provenance": {"output_kind": "graph_edges"},
            "source": f"symbol_function:src{i}.py:fn{i}",
            "target": "f:root", "type": "calls", "confidence": 1.0,
        })
    s.rebuild_from_output_records(manifest_hash="m", records=records)
    s._cached_payload = None
    res = compute_blast_radius(
        graph_store=s,
        seed_nodes=("f:root",),
        max_depth=1,
        node_cap=10,
    )
    assert "node_cap_reached" in res.warnings


def test_blast_radius_score_breakdown_is_documented():
    res = compute_blast_radius(
        graph_store=CodeCompassGraphStore(index_path="/nonexistent/index.json"),
        seed_nodes=(),
        max_depth=1,
    )
    assert set(res.score_breakdown) == {"files", "symbols", "tests_inverse", "heuristic"}


def test_blast_radius_seed_explains_change_warning(tmp_path):
    store = _seed_store(tmp_path / "y")
    res = compute_blast_radius(
        graph_store=store,
        seed_nodes=("symbol_function:file:a.py:func_a",),
        changed_files=("a.py",),
        max_depth=3,
    )
    assert "seed_explains_change" in res.warnings


def test_blast_radius_returns_finite_risk_score():
    store = CodeCompassGraphStore(index_path="/nonexistent/index.json")
    res = compute_blast_radius(graph_store=store, seed_nodes=())
    assert 0.0 <= res.risk_score <= 1.0