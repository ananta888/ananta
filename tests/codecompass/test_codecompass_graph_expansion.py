from __future__ import annotations

from worker.retrieval.codecompass_graph_expansion import expand_codecompass_graph
from worker.retrieval.codecompass_graph_store import CodeCompassGraphStore


def test_graph_expansion_is_deterministic_and_bounded(tmp_path):
    store = CodeCompassGraphStore(index_path=tmp_path / "cc_graph_index.json")
    store.rebuild_from_output_records(
        records=[
            {"id": "type:A", "kind": "java_type", "name": "A", "file": "src/A.java", "_provenance": {"output_kind": "graph_nodes"}},
            {"id": "method:A.m1", "kind": "java_method", "name": "m1", "file": "src/A.java", "_provenance": {"output_kind": "graph_nodes"}},
            {"id": "type:B", "kind": "java_type", "name": "B", "file": "src/B.java", "_provenance": {"output_kind": "graph_nodes"}},
            {"source": "method:A.m1", "target": "type:A", "type": "child_of_type", "_provenance": {"output_kind": "graph_edges"}},
            {"source": "method:A.m1", "target": "type:B", "type": "calls_probable_target", "_provenance": {"output_kind": "graph_edges"}},
            {"source": "type:B", "target": "method:A.m1", "type": "calls_probable_target", "_provenance": {"output_kind": "graph_edges"}},
        ],
        manifest_hash="mh-1",
    )

    first = expand_codecompass_graph(store=store, seed_node_ids=["method:A.m1"], profile="bugfix_local")
    second = expand_codecompass_graph(store=store, seed_node_ids=["method:A.m1"], profile="bugfix_local")

    assert first["profile"] == "bugfix_local"
    assert first["deterministic"] is True
    assert first["bounded"] is True
    assert first["nodes"] == second["nodes"]
    assert len(first["nodes"]) <= first["max_nodes"]
    assert "calls_probable_target" in first["allowed_edge_types"]

