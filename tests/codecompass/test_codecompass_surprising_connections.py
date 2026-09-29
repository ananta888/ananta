"""CRG-009: surprising connections tests."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from worker.retrieval.codecompass_graph_store import CodeCompassGraphStore
from worker.retrieval.codecompass_surprising_connections import (
    SURPRISING_CONNECTIONS_SCHEMA_VERSION,
    find_surprising_connections,
)


def _store(tmp_path: Path, records: list[dict]) -> CodeCompassGraphStore:
    s = CodeCompassGraphStore(index_path=tmp_path / "index.json")
    s.rebuild_from_output_records(manifest_hash="m", records=records)
    s._cached_payload = None
    return s


def _node(nid: str, kind: str, name: str, file: str, **attrs) -> dict:
    out = {
        "_provenance": {"output_kind": "graph_nodes"},
        "id": nid, "kind": kind, "name": name, "file": file,
    }
    out.update(attrs)
    return out


def test_surprising_connections_schema_version_is_pinned():
    assert SURPRISING_CONNECTIONS_SCHEMA_VERSION == "surprising_connections.v1"


def test_cross_domain_edge_is_a_surprise(tmp_path):
    s = _store(tmp_path / "x", [
        _node("f:auth", "symbol_function", "auth", "auth.py",
              attrs={"domain": "auth", "layer": "service", "language": "python"}),
        _node("f:db", "symbol_function", "query", "db.py",
              attrs={"domain": "persistence", "layer": "infra", "language": "python"}),
        {"_provenance": {"output_kind": "graph_edges"},
         "source": "f:auth", "target": "f:db",
         "type": "calls", "confidence": 0.9},
    ])
    res = find_surprising_connections(graph_store=s)
    assert any("cross_domain" in c.reason for c in res.candidates)


def test_cross_layer_edge_is_a_surprise(tmp_path):
    s = _store(tmp_path / "y", [
        _node("f:ui", "symbol_function", "ui", "ui.py",
              attrs={"domain": "ui", "layer": "presentation", "language": "python"}),
        _node("f:repo", "symbol_function", "repo", "repo.py",
              attrs={"domain": "ui", "layer": "data", "language": "python"}),
        {"_provenance": {"output_kind": "graph_edges"},
         "source": "f:ui", "target": "f:repo",
         "type": "calls", "confidence": 0.9},
    ])
    res = find_surprising_connections(graph_store=s)
    assert any("cross_layer" in c.reason for c in res.candidates)


def test_cross_language_edge_is_a_surprise(tmp_path):
    s = _store(tmp_path / "z", [
        _node("f:py", "symbol_function", "pyfn", "py.py",
              attrs={"language": "python", "layer": "service"}),
        _node("f:rs", "symbol_function", "rsfn", "rs.rs",
              attrs={"language": "rust", "layer": "service"}),
        {"_provenance": {"output_kind": "graph_edges"},
         "source": "f:py", "target": "f:rs",
         "type": "calls", "confidence": 0.8},
    ])
    res = find_surprising_connections(graph_store=s)
    assert any("cross_language" in c.reason for c in res.candidates)


def test_same_domain_layer_language_not_surprising(tmp_path):
    s = _store(tmp_path / "n", [
        _node("f:a", "symbol_function", "a", "a.py",
              attrs={"domain": "ui", "layer": "service", "language": "python"}),
        _node("f:b", "symbol_function", "b", "b.py",
              attrs={"domain": "ui", "layer": "service", "language": "python"}),
        {"_provenance": {"output_kind": "graph_edges"},
         "source": "f:a", "target": "f:b",
         "type": "calls", "confidence": 0.9},
    ])
    res = find_surprising_connections(graph_store=s)
    assert res.candidates == ()


def test_pure_name_heuristics_are_not_used(tmp_path):
    """An edge between two nodes without attrs must NOT produce a
    surprise candidate. (No name-only heuristic.)"""
    s = _store(tmp_path / "p", [
        _node("f:a", "symbol_function", "foo", "a.py"),
        _node("f:b", "symbol_function", "bar", "b.py"),
        {"_provenance": {"output_kind": "graph_edges"},
         "source": "f:a", "target": "f:b",
         "type": "calls", "confidence": 0.9},
    ])
    res = find_surprising_connections(graph_store=s)
    assert res.candidates == ()


def test_confidence_and_reason_are_emitted(tmp_path):
    s = _store(tmp_path / "c", [
        _node("f:auth", "symbol_function", "auth", "auth.py",
              attrs={"domain": "auth", "layer": "service", "language": "python"}),
        _node("f:db", "symbol_function", "db", "db.py",
              attrs={"domain": "db", "layer": "infra", "language": "python"}),
        {"_provenance": {"output_kind": "graph_edges"},
         "source": "f:auth", "target": "f:db",
         "type": "calls", "confidence": 0.6},
    ])
    res = find_surprising_connections(graph_store=s)
    c = res.candidates[0]
    assert c.confidence == 0.6
    # confidence_kind may be unset if the store did not promote it;
    # in that case we default to "EXTRACTED". Both are valid for
    # surprising-connection annotations (not policy-critical).
    assert c.confidence_kind in {"EXTRACTED", "INFERRED", "AMBIGUOUS", "MANUAL", ""}
    assert "cross_domain" in c.reason


def test_max_results_truncation(tmp_path):
    edges = []
    for i in range(20):
        edges.append(_node(f"f:a{i}", "symbol_function", f"a{i}", f"a{i}.py",
                          attrs={"domain": "auth", "layer": "x", "language": "py"}))
        edges.append(_node(f"f:b{i}", "symbol_function", f"b{i}", f"b{i}.py",
                          attrs={"domain": "db", "layer": "y", "language": "py"}))
        edges.append({"_provenance": {"output_kind": "graph_edges"},
                      "source": f"f:a{i}", "target": f"f:b{i}",
                      "type": "calls", "confidence": 0.9})
    s = _store(tmp_path / "t", edges)
    res = find_surprising_connections(graph_store=s, max_results=5)
    assert len(res.candidates) == 5
    assert "max_results_truncated" in res.warnings


def test_result_is_json_serialisable(tmp_path):
    s = _store(tmp_path / "j", [
        _node("f:a", "symbol_function", "a", "a.py",
              attrs={"domain": "auth", "layer": "x"}),
        _node("f:b", "symbol_function", "b", "b.py",
              attrs={"domain": "db", "layer": "y"}),
        {"_provenance": {"output_kind": "graph_edges"},
         "source": "f:a", "target": "f:b", "type": "calls"},
    ])
    res = find_surprising_connections(graph_store=s)
    json.dumps(res.as_dict())