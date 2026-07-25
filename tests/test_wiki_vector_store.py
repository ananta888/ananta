from __future__ import annotations

from worker.retrieval.embedding_provider import FakeEmbeddingProvider
from worker.retrieval.vector_store_config import (
    QdrantEndpointConfig,
    QdrantVectorStoreConfig,
)
from worker.retrieval.wiki_vector_store import (
    WIKI_VECTOR_PAYLOAD_SCHEMA,
    WikiVectorPayloadAdapter,
    WikiVectorStore,
    WikiVectorStoreConfig,
)


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


def test_wiki_payload_adapter_uses_separate_schema_and_collection_namespace():
    config = WikiVectorStoreConfig(
        workspace_id="workspace-a",
        source_id="wiki-de",
        profile_name="semantic",
    )
    payload = WikiVectorPayloadAdapter(config).adapt(
        {
            "record_id": "wiki:c1",
            "embedding_text": "Retry handling",
            "source_scope": "wiki",
        }
    )

    assert payload.payload_schema == WIKI_VECTOR_PAYLOAD_SCHEMA
    assert payload.domain == "wiki"
    assert payload.workspace_id == "workspace-a"
    assert payload.repository_id == "wiki-de"
    assert config.collection_scope().startswith("ananta-wiki-")


def test_wiki_qdrant_requires_separate_explicit_opt_in():
    import pytest

    with pytest.raises(ValueError, match="explicit_opt_in"):
        WikiVectorStoreConfig(provider="qdrant")

    qdrant = QdrantVectorStoreConfig(
        endpoint=QdrantEndpointConfig(),
        collection_prefix="ananta-wiki",
    )
    config = WikiVectorStoreConfig(
        provider="qdrant",
        qdrant_enabled=True,
        qdrant=qdrant,
    )
    assert config.provider == "qdrant"
    assert config.collection_prefix == "ananta-wiki"
