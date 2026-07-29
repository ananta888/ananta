from __future__ import annotations

from worker.retrieval.wiki_hybrid_engine import merge_wiki_hybrid_results


def test_wiki_hybrid_merge_keeps_score_components():
    merged = merge_wiki_hybrid_results(
        fts=[{"chunk_id": "wiki:c1", "score": 0.8}],
        vector=[{"chunk_id": "wiki:c1", "score": 0.6}],
        graph=[{"chunk_id": "wiki:c1", "score": 0.2}],
    )
    assert merged
    assert "score_components" in merged[0]
    assert merged[0]["score_components"]["fts"] == 0.8


def test_wiki_hybrid_merge_uses_fts_record_id_identity():
    merged = merge_wiki_hybrid_results(
        fts=[{"record_id": "wiki:c1", "score": 0.8}],
        vector=[{"record_id": "wiki:c1", "score": 0.6}],
        graph=[],
    )

    assert len(merged) == 1
    assert merged[0]["record_id"] == "wiki:c1"
    assert merged[0]["score_components"] == {
        "fts": 0.8,
        "vector": 0.6,
    }
