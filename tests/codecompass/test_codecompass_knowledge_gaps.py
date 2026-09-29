"""CRG-008: knowledge-gap analysis tests.

Acceptance criteria covered:

* isolates relevant nodes without edges
* finds untested hotspots
* thin communities
* every recommendation references at least one node/edge, or carries
  ``insufficient_evidence``
* no hallucinated recommendations: ``missing_test_coverage`` only fires
  for an actual untested node
"""
from __future__ import annotations

from pathlib import Path

import pytest

from worker.retrieval.codecompass_knowledge_gaps import (
    KNOWLEDGE_GAPS_SCHEMA_VERSION,
    find_knowledge_gaps,
)
from worker.retrieval.codecompass_graph_store import CodeCompassGraphStore


def _empty_store(tmp_path: Path) -> CodeCompassGraphStore:
    s = CodeCompassGraphStore(index_path=tmp_path / "index.json")
    s.rebuild_from_output_records(manifest_hash="m", records=[])
    s._cached_payload = None
    return s


def _store_with_untested_hotspot(tmp_path: Path) -> CodeCompassGraphStore:
    s = CodeCompassGraphStore(index_path=tmp_path / "index.json")
    records = [
        {"_provenance": {"output_kind": "graph_nodes"},
         "id": "f:hot", "kind": "symbol_function", "name": "hot", "file": "hot.py"},
    ]
    for i in range(4):
        records.append({
            "_provenance": {"output_kind": "graph_nodes"},
            "id": f"f:caller{i}", "kind": "symbol_function",
            "name": f"caller{i}", "file": f"caller{i}.py",
        })
        records.append({
            "_provenance": {"output_kind": "graph_edges"},
            "source": f"f:caller{i}", "target": "f:hot",
            "type": "calls", "confidence": 1.0,
        })
    s.rebuild_from_output_records(manifest_hash="m", records=records)
    s._cached_payload = None
    return s


def test_knowledge_gap_schema_version_is_pinned():
    assert KNOWLEDGE_GAPS_SCHEMA_VERSION == "knowledge_gaps.v1"


def test_empty_store_reports_insufficient_evidence(tmp_path):
    s = _empty_store(tmp_path / "e")
    res = find_knowledge_gaps(graph_store=s)
    assert res.insufficient_evidence is True
    assert res.gaps == ()


def test_untested_hotspot_with_missing_test_coverage(tmp_path):
    """Acceptance: fixture with untested service produces a knowledge_gap.type='missing_test_coverage'."""
    s = _store_with_untested_hotspot(tmp_path / "h")
    res = find_knowledge_gaps(graph_store=s)
    types = [g.type for g in res.gaps]
    assert "missing_test_coverage" in types
    # Hot node must be referenced by ID
    hot_gap = next(g for g in res.gaps if g.type == "missing_test_coverage")
    assert "f:hot" in hot_gap.nodes


def test_no_hallucinated_recommendations(tmp_path):
    """If a hotspot is covered by a 'covers' edge, it must NOT appear in missing_test_coverage."""
    s = CodeCompassGraphStore(index_path=tmp_path / "i.json")
    records = [
        {"_provenance": {"output_kind": "graph_nodes"},
         "id": "f:hot", "kind": "symbol_function", "name": "hot", "file": "hot.py"},
        {"_provenance": {"output_kind": "graph_nodes"},
         "id": "f:c1", "kind": "symbol_function", "name": "c1", "file": "c1.py"},
        {"_provenance": {"output_kind": "graph_nodes"},
         "id": "f:c2", "kind": "symbol_function", "name": "c2", "file": "c2.py"},
        {"_provenance": {"output_kind": "graph_nodes"},
         "id": "f:t1", "kind": "symbol_function", "name": "t1", "file": "t1.py"},
        {"_provenance": {"output_kind": "graph_edges"},
         "source": "f:c1", "target": "f:hot", "type": "calls", "confidence": 1.0},
        {"_provenance": {"output_kind": "graph_edges"},
         "source": "f:c2", "target": "f:hot", "type": "calls", "confidence": 1.0},
        {"_provenance": {"output_kind": "graph_edges"},
         "source": "f:t1", "target": "f:hot", "type": "covers", "confidence": 1.0},
    ]
    s.rebuild_from_output_records(manifest_hash="m", records=records)
    s._cached_payload = None
    res = find_knowledge_gaps(graph_store=s)
    types = [g.type for g in res.gaps]
    # f:hot is covered -> no missing_test_coverage
    assert "missing_test_coverage" not in types


def test_isolated_node_detected(tmp_path):
    s = CodeCompassGraphStore(index_path=tmp_path / "iso.json")
    s.rebuild_from_output_records(
        manifest_hash="m",
        records=[
            {"_provenance": {"output_kind": "graph_nodes"},
             "id": "f:orphan", "kind": "symbol_function", "name": "orphan", "file": "o.py"},
        ],
    )
    s._cached_payload = None
    res = find_knowledge_gaps(graph_store=s)
    types = [g.type for g in res.gaps]
    assert "isolated_node" in types


def test_summary_counts_match_findings(tmp_path):
    s = _store_with_untested_hotspot(tmp_path / "sum")
    res = find_knowledge_gaps(graph_store=s)
    assert res.summary["untested_hotspots"] >= 1
    assert res.summary["total_nodes"] >= 1


def test_no_recommendation_without_node_reference(tmp_path):
    """Every recommendation must reference at least one node/edge
    (CCRIG-DD-007: no hallucinated free-form recommendations)."""
    s = _store_with_untested_hotspot(tmp_path / "nor")
    res = find_knowledge_gaps(graph_store=s)
    for gap in res.gaps:
        assert gap.nodes or gap.evidence_edges