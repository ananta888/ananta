"""RIG-009: truth-precedence tests."""
from __future__ import annotations

from pathlib import Path

import pytest

from worker.retrieval.codecompass_graph_store import CodeCompassGraphStore
from worker.retrieval.codecompass_rig_truth_precedence import (
    TRUTH_PRECEDENCE_VERSION,
    decide_truth_precedence,
)


def _store(tmp_path: Path, *, rig_nodes=(), rig_edges=()) -> CodeCompassGraphStore:
    s = CodeCompassGraphStore(index_path=tmp_path / "index.json")
    records = list(rig_nodes) + list(rig_edges)
    s.rebuild_from_output_records(manifest_hash="m", records=records)
    s._cached_payload = None
    return s


def _rig_records_with(name: str):
    return [
        {"_provenance": {"output_kind": "rig_nodes"},
         "id": f"bc:{name}", "kind": "buildable_component",
         "attrs": {"name": name}},
    ]


def test_truth_precedence_version_is_pinned():
    assert TRUTH_PRECEDENCE_VERSION == "truth_precedence.v1"


def test_complete_rig_with_topic_is_strong(tmp_path):
    s = CodeCompassGraphStore(index_path=tmp_path / "i.json")
    # Manually inject rig + diagnostics to simulate complete coverage.
    s.rebuild_from_output_records(
        manifest_hash="m",
        records=[
            {"_provenance": {"output_kind": "rig_nodes"},
             "id": "bc:hello", "kind": "buildable_component",
             "attrs": {"name": "hello"}},
        ],
    )
    s._cached_payload = None
    # Override the coverage status to complete for this test
    p = s.load()
    p["diagnostics"]["repository_intelligence"]["coverage_status"] = "complete"
    s.save(p)
    s._cached_payload = None
    dec = decide_truth_precedence(graph_store=s, topic="hello", rag_present=True)
    assert dec.final_support == "strong"


def test_complete_rig_without_topic_with_rag_is_contradiction(tmp_path):
    s = _store(tmp_path / "c", rig_nodes=_rig_records_with("hello"))
    p = s.load()
    p["diagnostics"]["repository_intelligence"]["coverage_status"] = "complete"
    s.save(p)
    s._cached_payload = None
    dec = decide_truth_precedence(graph_store=s, topic="nonexistent", rag_present=True)
    assert dec.final_support == "contradiction"


def test_partial_rig_without_topic_with_rag_is_unknown_coverage(tmp_path):
    """RIG-009 acceptance: 'RAG says test exists, partial RIG finds none
    -> unknown_coverage'."""
    s = _store(tmp_path / "p", rig_nodes=_rig_records_with("hello"))
    p = s.load()
    p["diagnostics"]["repository_intelligence"]["coverage_status"] = "partial"
    s.save(p)
    s._cached_payload = None
    dec = decide_truth_precedence(graph_store=s, topic="nonexistent", rag_present=True)
    assert dec.final_support == "unknown_coverage"


def test_partial_rig_with_topic_is_weak_support(tmp_path):
    s = _store(tmp_path / "pw", rig_nodes=_rig_records_with("hello"))
    p = s.load()
    p["diagnostics"]["repository_intelligence"]["coverage_status"] = "partial"
    s.save(p)
    s._cached_payload = None
    dec = decide_truth_precedence(graph_store=s, topic="hello", rag_present=False)
    assert dec.final_support == "weak_support"


def test_no_rig_no_rag_is_unknown_coverage(tmp_path):
    s = _store(tmp_path / "n")
    dec = decide_truth_precedence(graph_store=s, topic="x", rag_present=False)
    assert dec.final_support == "unknown_coverage"


def test_no_rig_with_rag_is_weak_support(tmp_path):
    s = _store(tmp_path / "nr")
    dec = decide_truth_precedence(graph_store=s, topic="x", rag_present=True)
    assert dec.final_support == "weak_support"


def test_unknown_coverage_does_not_count_as_negative_evidence(tmp_path):
    """CCRIG-DD-008: missing at unknown status MUST NOT be negative.

    Concretely, the *decision itself* must not be a 'contradiction' (which
    is what RIG-009 reserves for negative evidence from complete RIG).
    """
    s = _store(tmp_path / "u", rig_nodes=_rig_records_with("hello"))
    p = s.load()
    p["diagnostics"]["repository_intelligence"]["coverage_status"] = "unknown"
    s.save(p)
    s._cached_payload = None
    dec = decide_truth_precedence(graph_store=s, topic="nope", rag_present=True)
    assert dec.final_support == "unknown_coverage"
    assert dec.final_support != "contradiction"


def test_decision_records_topic_and_status(tmp_path):
    s = _store(tmp_path / "t")
    dec = decide_truth_precedence(graph_store=s, topic="abc", rag_present=False)
    assert dec.topic == "abc"
    assert dec.rig_status in {"unavailable", "unknown", "partial", "complete"}


def test_complete_rig_warns_when_rag_present(tmp_path):
    s = _store(tmp_path / "w", rig_nodes=_rig_records_with("hello"))
    p = s.load()
    p["diagnostics"]["repository_intelligence"]["coverage_status"] = "complete"
    s.save(p)
    s._cached_payload = None
    dec = decide_truth_precedence(graph_store=s, topic="hello", rag_present=True)
    assert "rag_present_but_rig_authoritative" in dec.warnings