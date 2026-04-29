from __future__ import annotations

from worker.retrieval.codecompass_vector_engine import CodeCompassVectorEngine
from worker.retrieval.codecompass_vector_store import CodeCompassVectorStore
from worker.retrieval.embedding_provider import FakeEmbeddingProvider, OpenAICompatibleEmbeddingProvider
from worker.retrieval.retrieval_service import HybridRetrievalService


def test_worker_retrieval_uses_codecompass_vector_channel_when_enabled(tmp_path):
    store = CodeCompassVectorStore(index_path=tmp_path / "cc_vector_index.json")
    provider = FakeEmbeddingProvider(model_version="fake-v1", dimensions=6)
    store.rebuild(
        documents=[
            {
                "record_id": "rec-1",
                "kind": "java_method",
                "file": "src/PaymentService.java",
                "manifest_hash": "mh-1",
                "embedding_text": "payment retry timeout handling",
            }
        ],
        embedding_provider=provider,
        retrieval_cache_state="cache-1",
        manifest_hash="mh-1",
    )
    engine = CodeCompassVectorEngine(store=store, embedding_provider=provider)
    vector_chunks = engine.search(query="retry timeout payment", retrieval_intent="fuzzy_semantic", top_k=3)
    service = HybridRetrievalService()

    payload = service.retrieve(
        query="retry timeout payment",
        pipeline_contract={
            "channels": ["lexical", "codecompass_vector"],
            "fallback_order": ["codecompass_vector", "lexical"],
        },
        channel_results={
            "codecompass_vector": [
                {
                    "path": str(item.get("source") or ""),
                    "content_hash": str((item.get("metadata") or {}).get("record_id") or ""),
                    "record_id": str((item.get("metadata") or {}).get("record_id") or ""),
                    "score": float(item.get("score") or 0.0),
                    "metadata": dict(item.get("metadata") or {}),
                }
                for item in vector_chunks
            ],
            "lexical": [{"path": "docs/payment.md", "content_hash": "lex-1", "score": 0.2}],
        },
        channel_config={"codecompass_vector": True},
        top_k=2,
    )

    assert "codecompass_vector" in payload["used_channels"]
    assert payload["channel_diagnostics"]["codecompass_vector"]["status"] == "ready"
    assert payload["selected"]
    first = payload["selected"][0]
    assert first["channel"] in {"codecompass_vector", "lexical"}


def test_worker_retrieval_degrades_vector_channel_on_provider_failure(tmp_path):
    store = CodeCompassVectorStore(index_path=tmp_path / "cc_vector_index.json")
    store.save(state={"schema": "codecompass_vector_index.v1"}, entries=[])
    failing_provider = OpenAICompatibleEmbeddingProvider(base_url="", api_key=None)
    engine = CodeCompassVectorEngine(store=store, embedding_provider=failing_provider)
    vector_chunks = engine.search(query="retry timeout payment", top_k=3)
    service = HybridRetrievalService()

    payload = service.retrieve(
        query="retry timeout payment",
        pipeline_contract={
            "channels": ["lexical", "codecompass_vector"],
            "fallback_order": ["codecompass_vector", "lexical"],
        },
        channel_results={
            "codecompass_vector": vector_chunks,
            "lexical": [{"path": "docs/payment.md", "content_hash": "lex-1", "score": 0.4}],
        },
        channel_errors={"codecompass_vector": "embedding_provider_failure"},
        channel_config={"codecompass_vector": True},
        top_k=2,
    )

    assert payload["channel_diagnostics"]["codecompass_vector"]["status"] == "degraded"
    assert payload["channel_diagnostics"]["codecompass_vector"]["reason"] == "embedding_provider_failure"
    assert payload["used_channels"] == ["lexical"]
    assert payload["selected"][0]["path"] == "docs/payment.md"

