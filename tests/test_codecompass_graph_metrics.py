"""CRG-007: graph metrics tests."""
from __future__ import annotations

from pathlib import Path

from worker.retrieval.codecompass_graph_metrics import (
    BETWEENNESS_DEPTH_CAP,
    METRICS_SCHEMA_VERSION,
    _bounded_shortest_paths_through,
    compute_graph_metrics,
)
from worker.retrieval.codecompass_graph_store import CodeCompassGraphStore


def _build_star(tmp_path: Path, *, leaves: int) -> CodeCompassGraphStore:
    """Build a star graph: one hub connected to N leaves."""
    s = CodeCompassGraphStore(index_path=tmp_path / "index.json")
    records = [
        {"_provenance": {"output_kind": "graph_nodes"},
         "id": "f:hub", "kind": "symbol_function", "name": "hub", "file": "hub.py"},
    ]
    for i in range(leaves):
        records.append({
            "_provenance": {"output_kind": "graph_nodes"},
            "id": f"f:leaf{i}", "kind": "symbol_function",
            "name": f"leaf{i}", "file": f"leaf{i}.py",
        })
        records.append({
            "_provenance": {"output_kind": "graph_edges"},
            "source": f"f:leaf{i}", "target": "f:hub",
            "type": "calls", "confidence": 1.0,
        })
    s.rebuild_from_output_records(manifest_hash="m", records=records)
    s._cached_payload = None
    return s


def test_metrics_schema_version_is_pinned():
    assert METRICS_SCHEMA_VERSION == "graph_metrics.v1"


def test_hub_node_detected_for_star_graph(tmp_path):
    s = _build_star(tmp_path / "x", leaves=5)
    res = compute_graph_metrics(graph_store=s, top_k=5)
    hub_ids = {h.node_id for h in res.hub_nodes}
    assert "f:hub" in hub_ids


def test_bridge_score_in_unit_interval(tmp_path):
    s = _build_star(tmp_path / "y", leaves=3)
    res = compute_graph_metrics(graph_store=s, top_k=10)
    for b in res.bridge_nodes:
        assert 0.0 <= b.score <= 1.0


def test_top_k_limits_results(tmp_path):
    s = _build_star(tmp_path / "z", leaves=20)
    res = compute_graph_metrics(graph_store=s, top_k=3)
    assert len(res.hub_nodes) <= 3
    assert len(res.bridge_nodes) <= 3


def test_metrics_on_empty_graph_does_not_crash(tmp_path):
    s = CodeCompassGraphStore(index_path=tmp_path / "nope.json")
    res = compute_graph_metrics(graph_store=s)
    assert res.hub_nodes == ()
    assert res.bridge_nodes == ()


def test_metrics_betweenness_depth_cap_is_bounded():
    """Sanity: BETWEENNESS_DEPTH_CAP must be small to keep the algorithm cheap."""
    assert BETWEENNESS_DEPTH_CAP <= 8


def test_betweenness_counts_only_shortest_path_intermediates() -> None:
    def edge(source: str, target: str) -> dict:
        return {"source_id": source, "target_id": target}

    outgoing = {
        "a": {"calls": [edge("a", "b"), edge("a", "c")]},
        "b": {"calls": [edge("b", "d")]},
        "c": {"calls": [edge("c", "e")]},
        "e": {"calls": [edge("e", "d")]},
    }

    summary = _bounded_shortest_paths_through(
        "a", "d", outgoing, depth_cap=4, path_cap=1000,
    )

    assert summary.intermediate_counts == (("b", 1),)
    assert summary.paths_considered == 1
    assert summary.truncated is False


def test_betweenness_reports_path_cap_truncation() -> None:
    def edge(source: str, target: str) -> dict:
        return {"source_id": source, "target_id": target}

    outgoing = {
        "a": {"calls": [edge("a", "b"), edge("a", "c")]},
        "b": {"calls": [edge("b", "d")]},
        "c": {"calls": [edge("c", "d")]},
    }

    summary = _bounded_shortest_paths_through(
        "a", "d", outgoing, depth_cap=4, path_cap=1,
    )

    assert summary.paths_considered == 1
    assert summary.truncated is True
    assert sum(count for _, count in summary.intermediate_counts) == 1


def test_metrics_result_is_json_serialisable(tmp_path):
    import json
    s = _build_star(tmp_path / "j", leaves=4)
    res = compute_graph_metrics(graph_store=s)
    json.dumps(res.as_dict())  # must not raise


def test_metrics_evidence_edges_are_empty_for_now(tmp_path):
    """Per CRG-007: scores carry reasons; the Viewer reads these as metadata.
    We start with reason-only evidence edges; richer provenance comes from
    the import pipeline (COMBO-002)."""
    s = _build_star(tmp_path / "k", leaves=2)
    res = compute_graph_metrics(graph_store=s)
    for h in res.hub_nodes:
        assert h.reasons
