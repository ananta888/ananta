from __future__ import annotations

from worker.retrieval.codecompass_graph_context import build_graph_context_chunks


def test_graph_context_builder_groups_seed_and_neighbors_with_clipped_content():
    chunks = build_graph_context_chunks(
        expansion={
            "profile": "bugfix_local",
            "seed_node_ids": ["method:A.m1"],
            "nodes": [
                {
                    "id": "method:A.m1",
                    "kind": "java_method",
                    "record_id": "method:A.m1",
                    "file": "src/A.java",
                    "content": "seed chunk",
                },
                {
                    "id": "type:B",
                    "kind": "java_type",
                    "record_id": "type:B",
                    "file": "src/B.java",
                    "content": "neighbor chunk with secret sk-secret-token-1234567890",
                },
            ],
            "paths": [
                {
                    "node_id": "type:B",
                    "path": [{"source_id": "method:A.m1", "target_id": "type:B", "edge_type": "calls_probable_target"}],
                }
            ],
        },
        max_content_chars=60,
    )

    assert len(chunks) == 2
    assert chunks[0]["metadata"]["group"] == "seed"
    assert chunks[1]["metadata"]["group"] == "expanded_neighbor"
    assert chunks[1]["metadata"]["expanded_from"] == "method:A.m1"
    assert chunks[1]["metadata"]["relation_path"] == "calls_probable_target"
    assert "sk-secret-token-1234567890" not in chunks[1]["content"]

