from __future__ import annotations

from worker.retrieval.embedding_provider import FakeEmbeddingProvider
from worker.retrieval.wiki_vector_store import WikiVectorStore


def test_wiki_vector_store_build_and_query(tmp_path):
    store = WikiVectorStore(index_path=tmp_path / "wiki_vector.json")
    provider = FakeEmbeddingProvider(model_version="wiki-fake-v1", dimensions=6)
    docs = [
        {"record_id": "wiki:c1", "kind": "wiki_section_chunk", "file": "wiki/payment.md", "manifest_hash": "mh-wiki-1", "embedding_text": "Ananta retry handling", "source_scope": "wiki"},
        {"record_id": "wiki:c2", "kind": "wiki_section_chunk", "file": "wiki/auth.md", "manifest_hash": "mh-wiki-1", "embedding_text": "Token verification", "source_scope": "wiki"},
    ]
    store.rebuild(documents=docs, embedding_provider=provider, retrieval_cache_state="wiki-cache-1", manifest_hash="mh-wiki-1")
    hits = store.search(query="retry", embedding_provider=provider, top_k=2)
    assert isinstance(hits, list)
