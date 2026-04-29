from __future__ import annotations

from worker.retrieval.codecompass_graph_store import CodeCompassGraphStore


def test_codecompass_graph_store_loads_nodes_edges_and_indexes(tmp_path):
    store = CodeCompassGraphStore(index_path=tmp_path / "cc_graph_index.json")
    diagnostics = store.rebuild_from_output_records(
        records=[
            {
                "id": "n1",
                "kind": "java_type",
                "name": "PaymentService",
                "file": "src/PaymentService.java",
                "_provenance": {"output_kind": "graph_nodes"},
            },
            {
                "id": "n2",
                "kind": "java_method",
                "name": "retryTimeout",
                "file": "src/PaymentService.java",
                "_provenance": {"output_kind": "graph_nodes"},
            },
            {
                "source": "n2",
                "target": "n1",
                "type": "child_of_type",
                "_provenance": {"output_kind": "graph_edges"},
            },
        ],
        manifest_hash="mh-1",
    )
    loaded = store.load()
    traversal = store.traverse(seed_ids=["n2"], max_depth=2, max_nodes=5, allowed_edge_types={"child_of_type"})

    assert diagnostics["status"] == "ready"
    by_id = dict((loaded["node_index"] or {}).get("by_id") or {})
    assert "n1" in by_id and "n2" in by_id
    assert loaded["outgoing_index"]["n2"]["child_of_type"][0]["target_id"] == "n1"
    assert loaded["incoming_index"]["n1"]["child_of_type"][0]["source_id"] == "n2"
    assert traversal["cycle_guarded"] is True
    assert traversal["bounded"] is True
    assert [node["id"] for node in traversal["nodes"]] == ["n2", "n1"]


def test_codecompass_graph_store_degrades_when_graph_outputs_missing(tmp_path):
    store = CodeCompassGraphStore(index_path=tmp_path / "cc_graph_index.json")
    diagnostics = store.rebuild_from_output_records(
        records=[
            {
                "id": "n1",
                "kind": "java_type",
                "name": "PaymentService",
                "file": "src/PaymentService.java",
                "_provenance": {"output_kind": "graph_nodes"},
            }
        ],
        manifest_hash="mh-1",
    )

    assert diagnostics["status"] == "degraded"
    assert diagnostics["reason"] == "missing_graph_outputs"

