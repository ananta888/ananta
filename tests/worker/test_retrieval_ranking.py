from __future__ import annotations

from worker.retrieval.reranker import Reranker
from worker.retrieval.retrieval_service import HybridRetrievalService


def test_hybrid_retrieval_merges_channels_with_rationale() -> None:
    service = HybridRetrievalService()
    payload = service.retrieve(
        query="fix auth bug",
        pipeline_contract={"channels": ["dense", "lexical", "symbol"], "fallback_order": ["dense", "lexical", "symbol"]},
        channel_results={
            "dense": [{"path": "src/auth.py", "content_hash": "h-auth", "score": 0.9, "text": "auth bug fix"}],
            "lexical": [{"path": "src/api.py", "content_hash": "h-api", "score": 0.8, "text": "api auth route"}],
            "symbol": [{"path": "src/auth.py", "content_hash": "h-auth", "score": 0.7, "symbol_name": "login"}],
        },
        task_type="bugfix",
        profile="balanced",
        top_k=2,
    )
    assert payload["schema"] == "retrieval_selection.v1"
    assert payload["query_original"] == "fix auth bug"
    assert payload["query_rewritten"]
    assert payload["selected"][0]["rationale"]["profile"] == "balanced"


def test_optional_reranker_can_reorder_candidates() -> None:
    service = HybridRetrievalService(reranker=Reranker(enabled=True, weight=1.0))
    payload = service.retrieve(
        query="auth login",
        pipeline_contract={"channels": ["dense", "lexical"], "fallback_order": ["dense", "lexical"]},
        channel_results={
            "dense": [
                {"path": "src/a.py", "content_hash": "a", "score": 0.6, "text": "login auth flow"},
                {"path": "src/b.py", "content_hash": "b", "score": 0.6, "text": "metrics collector"},
            ],
            "lexical": [],
        },
        top_k=2,
    )
    assert len(payload["selected"]) == 2
    assert payload["selected"][0]["path"] == "src/a.py"

