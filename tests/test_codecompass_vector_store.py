from __future__ import annotations

from worker.retrieval.codecompass_vector_store import CodeCompassVectorStore
from worker.retrieval.embedding_provider import FakeEmbeddingProvider


def test_codecompass_vector_store_rebuild_and_search(tmp_path):
    store = CodeCompassVectorStore(index_path=tmp_path / "cc_vector_index.json")
    provider = FakeEmbeddingProvider(model_version="fake-v2", dimensions=5)
    documents = [
        {
            "record_id": "r1",
            "kind": "java_method",
            "file": "src/PaymentService.java",
            "parent_id": "t1",
            "role_labels": ["service"],
            "importance_score": 2.0,
            "source_scope": "repo",
            "profile_name": "java",
            "manifest_hash": "mh-1",
            "embedding_text": "payment retry timeout method",
        },
        {
            "record_id": "r2",
            "kind": "java_method",
            "file": "src/InvoiceService.java",
            "parent_id": "t2",
            "role_labels": ["service"],
            "importance_score": 1.0,
            "source_scope": "repo",
            "profile_name": "java",
            "manifest_hash": "mh-1",
            "embedding_text": "invoice creation and tax calculation",
        },
    ]

    rebuild = store.rebuild(
        documents=documents,
        embedding_provider=provider,
        retrieval_cache_state="cache-1",
        manifest_hash="mh-1",
    )
    loaded = store.load()
    results = store.search(query="payment timeout", embedding_provider=provider, top_k=2)

    assert rebuild["status"] == "ok"
    assert loaded["state"]["embedding_model_name"] == "fake-v2"
    assert loaded["state"]["embedding_dimensions"] == 5
    assert loaded["state"]["manifest_hash"] == "mh-1"
    assert len(results) == 2
    assert results[0]["record_id"] in {"r1", "r2"}
    assert "source_manifest_hash" in results[0]


def test_codecompass_vector_store_refresh_rebuilds_when_manifest_changes(tmp_path):
    store = CodeCompassVectorStore(index_path=tmp_path / "cc_vector_index.json")
    provider = FakeEmbeddingProvider(model_version="fake-v1", dimensions=4)
    documents = [
        {
            "record_id": "r1",
            "kind": "java_method",
            "file": "src/PaymentService.java",
            "manifest_hash": "mh-1",
            "embedding_text": "payment retry timeout method",
        }
    ]
    store.rebuild(
        documents=documents,
        embedding_provider=provider,
        retrieval_cache_state="cache-1",
        manifest_hash="mh-1",
    )

    unchanged = store.refresh(
        documents=documents,
        embedding_provider=provider,
        retrieval_cache_state="cache-1",
        manifest_hash="mh-1",
    )
    rebuilt = store.refresh(
        documents=documents,
        embedding_provider=provider,
        retrieval_cache_state="cache-2",
        manifest_hash="mh-2",
    )

    assert unchanged["mode"] == "unchanged"
    assert rebuilt["mode"] == "rebuild"

