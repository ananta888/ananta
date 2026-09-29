"""RIG-002: RIG payload slot in CodeCompassGraphStore.

Tests assert:

1. ``load()`` returns stable ``rig_nodes``, ``rig_edges``, ``rig_index`` and
   ``diagnostics.repository_intelligence`` keys even when the on-disk
   payload is missing.
2. ``rebuild_from_output_records`` accepts ``output_kind='rig_nodes'`` and
   ``output_kind='rig_edges'``.
3. Existing symbol-graph tests continue to pass (covered by
   ``test_codecompass_graph_store.py``).
4. The RIG slot never enters the symbolgraph ``node_index`` /
   ``outgoing_index`` / ``incoming_index`` (CCRIG-DD-006).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from worker.retrieval.codecompass_graph_store import CodeCompassGraphStore


def test_load_missing_index_returns_stable_rig_slot(tmp_path):
    store = CodeCompassGraphStore(index_path=tmp_path / "nope.json")
    payload = store.load()
    assert "rig_nodes" in payload
    assert "rig_edges" in payload
    assert "rig_index" in payload
    assert payload["rig_nodes"] == []
    assert payload["rig_edges"] == []
    assert payload["rig_index"]["schema"] == "codecompass_repository_intelligence.v1"
    assert payload["rig_index"]["node_count"] == 0
    assert "repository_intelligence" in payload["diagnostics"]
    assert payload["diagnostics"]["repository_intelligence"]["status"] == "degraded"


def test_rebuild_accepts_rig_nodes_and_edges(tmp_path):
    store = CodeCompassGraphStore(index_path=tmp_path / "index.json")
    diag = store.rebuild_from_output_records(
        manifest_hash="m1",
        records=[
            {
                "_provenance": {"output_kind": "rig_nodes"},
                "id": "bc:hello",
                "kind": "buildable_component",
                "name": "hello",
            },
            {
                "_provenance": {"output_kind": "rig_nodes"},
                "id": "rn:ctest",
                "kind": "runner",
            },
            {
                "_provenance": {"output_kind": "rig_edges"},
                "from_id": "bc:hello",
                "to_id": "rn:ctest",
                "kind": "tested_by",
            },
        ],
    )
    payload = store.load()
    assert len(payload["rig_nodes"]) == 2
    assert len(payload["rig_edges"]) == 1
    assert payload["rig_index"]["node_count"] == 2
    assert payload["rig_index"]["edge_count"] == 1
    assert payload["rig_index"]["nodes_by_id"]["bc:hello"]["kind"] == "buildable_component"
    assert diag["repository_intelligence"]["status"] == "ready"


def test_rig_slot_does_not_pollute_symbolgraph_indexes(tmp_path):
    """CCRIG-DD-006: rig_nodes must not appear in node_index/outgoing/incoming."""
    store = CodeCompassGraphStore(index_path=tmp_path / "index.json")
    store.rebuild_from_output_records(
        manifest_hash="m1",
        records=[
            {
                "_provenance": {"output_kind": "rig_nodes"},
                "id": "bc:hello",
                "kind": "buildable_component",
            },
        ],
    )
    payload = store.load()
    assert "bc:hello" not in payload["node_index"].get("by_id", {})
    assert "bc:hello" not in (payload["outgoing_index"] or {})
    assert "bc:hello" not in (payload["incoming_index"] or {})


def test_rig_diagnostics_reason_when_no_records(tmp_path):
    store = CodeCompassGraphStore(index_path=tmp_path / "index.json")
    store.rebuild_from_output_records(manifest_hash="m1", records=[])
    payload = store.load()
    assert payload["diagnostics"]["repository_intelligence"]["status"] == "degraded"
    assert payload["diagnostics"]["repository_intelligence"]["reason"] == "no_rig_records"


def test_rig_slot_persists_through_save_load_cycle(tmp_path):
    """Round-trip: build with rig records, save, reload, verify stable."""
    index_path = tmp_path / "index.json"
    store = CodeCompassGraphStore(index_path=index_path)
    store.rebuild_from_output_records(
        manifest_hash="m1",
        records=[
            {"_provenance": {"output_kind": "rig_nodes"}, "id": "bc:x", "kind": "buildable_component"},
        ],
    )
    # clear cache to force re-read from disk
    store._cached_payload = None
    payload = store.load()
    assert len(payload["rig_nodes"]) == 1
    assert payload["rig_index"]["nodes_by_id"]["bc:x"]["kind"] == "buildable_component"


def test_existing_x86_slot_unchanged(tmp_path):
    """Regression: existing x86 extension still works alongside the new rig slot."""
    store = CodeCompassGraphStore(index_path=tmp_path / "index.json")
    store.rebuild_from_output_records(
        manifest_hash="m1",
        records=[
            {"_provenance": {"output_kind": "x86_nodes"}, "id": "x86_inst:1", "kind": "instruction"},
        ],
    )
    payload = store.load()
    assert len(payload["x86_nodes"]) == 1
    assert "rig_index" in payload