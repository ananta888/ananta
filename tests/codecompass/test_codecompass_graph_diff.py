"""CRG-010: graph-diff tests."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from worker.retrieval.codecompass_graph_diff import (
    GRAPH_DIFF_VERSION,
    diff_snapshots,
)
from worker.retrieval.codecompass_graph_store import CodeCompassGraphStore


def _store_with(tmp_path: Path, name: str, records: list[dict]) -> CodeCompassGraphStore:
    p = tmp_path / name
    s = CodeCompassGraphStore(index_path=p)
    s.rebuild_from_output_records(manifest_hash=name, records=records)
    s._cached_payload = None
    return s


def _records_a() -> list[dict]:
    return [
        {"_provenance": {"output_kind": "graph_nodes"},
         "id": "f:a", "kind": "file", "name": "a", "file": "a.py"},
        {"_provenance": {"output_kind": "graph_nodes"},
         "id": "f:b", "kind": "file", "name": "b", "file": "b.py"},
        {"_provenance": {"output_kind": "graph_nodes"},
         "id": "symbol_function:f:a:f1", "kind": "symbol_function",
         "name": "f1", "file": "a.py"},
        {"_provenance": {"output_kind": "graph_edges"},
         "source": "symbol_function:f:a:f1", "target": "f:a",
         "type": "calls", "confidence": 1.0},
    ]


def _records_b_added_node() -> list[dict]:
    return _records_a() + [
        {"_provenance": {"output_kind": "graph_nodes"},
         "id": "symbol_function:f:b:f2", "kind": "symbol_function",
         "name": "f2", "file": "b.py"},
    ]


def _records_b_removed_node() -> list[dict]:
    """Variant of A where the symbol_function is removed; the calls edge
    becomes a dangling reference and is dropped by the store (no
    endpoints)."""
    return [
        {"_provenance": {"output_kind": "graph_nodes"},
         "id": "f:a", "kind": "file", "name": "a", "file": "a.py"},
        {"_provenance": {"output_kind": "graph_nodes"},
         "id": "f:b", "kind": "file", "name": "b", "file": "b.py"},
    ]


def _records_b_changed_confidence() -> list[dict]:
    return [
        {"_provenance": {"output_kind": "graph_nodes"},
         "id": "f:a", "kind": "file", "name": "a", "file": "a.py"},
        {"_provenance": {"output_kind": "graph_nodes"},
         "id": "f:b", "kind": "file", "name": "b", "file": "b.py"},
        {"_provenance": {"output_kind": "graph_nodes"},
         "id": "symbol_function:f:a:f1", "kind": "symbol_function",
         "name": "f1", "file": "a.py"},
        {"_provenance": {"output_kind": "graph_edges"},
         "source": "symbol_function:f:a:f1", "target": "f:a",
         "type": "calls", "confidence": 0.5},
    ]


def test_graph_diff_version_is_pinned():
    assert GRAPH_DIFF_VERSION == "graph_diff.v1"


def test_diff_detects_added_node(tmp_path):
    base = _store_with(tmp_path / "a", "a.json", _records_a())
    target = _store_with(tmp_path / "b", "b.json", _records_b_added_node())
    diff = diff_snapshots(base=base, target=target)
    assert "symbol_function:f:b:f2" in diff.added_node_ids
    assert diff.removed_node_ids == ()


def test_diff_detects_removed_node(tmp_path):
    base = _store_with(tmp_path / "a", "a.json", _records_a())
    target = _store_with(tmp_path / "b", "b.json", _records_b_removed_node())
    diff = diff_snapshots(base=base, target=target)
    assert "symbol_function:f:a:f1" in diff.removed_node_ids


def test_diff_detects_changed_confidence_on_edge(tmp_path):
    base = _store_with(tmp_path / "a", "a.json", _records_a())
    target = _store_with(tmp_path / "b", "b.json", _records_b_changed_confidence())
    diff = diff_snapshots(base=base, target=target)
    assert len(diff.changed_edges) == 1


def test_diff_is_deterministic(tmp_path):
    base = _store_with(tmp_path / "a", "a.json", _records_a())
    target = _store_with(tmp_path / "b", "b.json", _records_b_added_node())
    d1 = diff_snapshots(base=base, target=target)
    d2 = diff_snapshots(base=base, target=target)
    assert d1.as_dict() == d2.as_dict()


def test_diff_provenance_includes_manifest_hash(tmp_path):
    base = _store_with(tmp_path / "a", "a.json", _records_a())
    target = _store_with(tmp_path / "b", "b.json", _records_a())
    diff = diff_snapshots(base=base, target=target)
    assert diff.base_provenance["manifest_hash"] == "a.json"
    assert diff.target_provenance["manifest_hash"] == "b.json"


def test_diff_works_without_crg_installed(tmp_path):
    """CRG-010 acceptance: works without CRG installation."""
    base = _store_with(tmp_path / "a", "a.json", _records_a())
    target = _store_with(tmp_path / "b", "b.json", _records_a())
    # No CRG symbols in source — diff just compares the local payloads
    diff = diff_snapshots(base=base, target=target)
    assert diff.added_node_ids == ()
    assert diff.removed_node_ids == ()


def test_diff_result_is_json_serialisable(tmp_path):
    base = _store_with(tmp_path / "a", "a.json", _records_a())
    target = _store_with(tmp_path / "b", "b.json", _records_b_added_node())
    diff = diff_snapshots(base=base, target=target)
    json.dumps(diff.as_dict())


def test_diff_handles_no_changes(tmp_path):
    base = _store_with(tmp_path / "a", "a.json", _records_a())
    target = _store_with(tmp_path / "b", "b.json", _records_a())
    diff = diff_snapshots(base=base, target=target)
    assert diff.added_node_ids == ()
    assert diff.removed_node_ids == ()
    assert diff.changed_node_ids == ()
    assert diff.added_edges == ()
    assert diff.removed_edges == ()