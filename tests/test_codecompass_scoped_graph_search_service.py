from agent.services.codecompass_scoped_graph_search_service import (
    CodeCompassScopedGraphSearchService,
)


def test_graph_search_ranks_matches_and_expands_bounded_neighbors() -> None:
    rows = CodeCompassScopedGraphSearchService._rank_and_expand(
        query="CodeCompass retrieval",
        nodes=[
            {"id": "repo", "kind": "repository", "name": "Ananta"},
            {"id": "retrieval", "kind": "source_file", "name": "codecompass_retrieval.py", "file": "agent/services/codecompass_retrieval.py"},
            {"id": "worker", "kind": "source_file", "name": "worker.py", "file": "worker/retrieval/worker.py"},
        ],
        edges=[
            {"source_id": "retrieval", "target_id": "worker", "edge_type": "calls"},
        ],
        limit=3,
        depth=1,
    )

    assert [row["id"] for row in rows] == ["retrieval", "worker"]
    assert rows[0]["path"] == "agent/services/codecompass_retrieval.py"
    assert "calls" in rows[0]["content"]
