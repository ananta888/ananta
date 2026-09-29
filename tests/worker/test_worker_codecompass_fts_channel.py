from __future__ import annotations

from worker.retrieval.retrieval_service import HybridRetrievalService


def test_worker_retrieval_uses_codecompass_fts_channel_when_enabled():
    service = HybridRetrievalService()
    payload = service.retrieve(
        query="PaymentService timeout",
        pipeline_contract={
            "channels": ["lexical", "codecompass_fts", "symbol"],
            "fallback_order": ["codecompass_fts", "lexical", "symbol"],
        },
        channel_results={
            "codecompass_fts": [{"path": "src/PaymentService.java", "content_hash": "h1", "score": 0.9}],
            "lexical": [{"path": "docs/payment.md", "content_hash": "h2", "score": 0.6}],
            "symbol": [],
        },
        channel_config={"codecompass_fts": True},
        top_k=2,
    )
    assert payload["schema"] == "retrieval_selection.v1"
    assert "codecompass_fts" in payload["used_channels"]
    assert payload["channel_diagnostics"]["codecompass_fts"]["status"] == "ready"
    assert payload["provenance"][0]["engine"] in {"codecompass_fts", "lexical"}


def test_worker_retrieval_falls_back_when_codecompass_fts_disabled():
    service = HybridRetrievalService()
    payload = service.retrieve(
        query="PaymentService timeout",
        pipeline_contract={
            "channels": ["lexical", "codecompass_fts", "symbol"],
            "fallback_order": ["codecompass_fts", "lexical", "symbol"],
        },
        channel_results={
            "codecompass_fts": [{"path": "src/PaymentService.java", "content_hash": "h1", "score": 0.9}],
            "lexical": [{"path": "docs/payment.md", "content_hash": "h2", "score": 0.6}],
            "symbol": [],
        },
        channel_config={"codecompass_fts": False},
        top_k=2,
    )
    assert payload["channel_diagnostics"]["codecompass_fts"]["status"] == "disabled"
    assert "codecompass_fts" not in payload["used_channels"]
    assert payload["used_channels"] == ["lexical"]
    assert payload["selected"][0]["path"] == "docs/payment.md"

