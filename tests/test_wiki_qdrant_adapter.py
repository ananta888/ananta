from __future__ import annotations

from worker.retrieval.embedding_provider import FakeEmbeddingProvider
from worker.retrieval.vector_store_contract import (
    IndexWriteResult,
    VectorSearchResult,
)
from worker.retrieval.wiki_vector_store import (
    WikiPreparedVectorBackend,
    WikiVectorPayloadAdapter,
    WikiVectorStoreConfig,
)


class _CaptureStore:
    def __init__(self) -> None:
        self.query = None

    def search_by_vector(self, query):
        self.query = query
        return VectorSearchResult(hits=(), reason="empty_collection")

    def rebuild(self, points, *, compatibility):
        del points, compatibility
        return IndexWriteResult("ok", "rebuild", "rebuild", 0)

    def refresh(self, points, *, compatibility):
        del points, compatibility
        return IndexWriteResult("ok", "refresh", "unchanged", 0)

    def upsert(self, points, *, batch_size=128):
        del points, batch_size
        return IndexWriteResult("ok", "upsert", "upsert", 0)

    def delete(self, point_ids, *, scope):
        del point_ids, scope
        return IndexWriteResult("ok", "delete", "delete", 0)

    def diagnostics(self):
        raise AssertionError("not used")

    def close(self):
        return None


def test_wiki_adapter_enforces_trusted_scope_and_server_filters() -> None:
    config = WikiVectorStoreConfig(
        workspace_id="trusted-workspace",
        source_id="trusted-source",
        profile_name="semantic",
    )
    capture = _CaptureStore()
    backend = WikiPreparedVectorBackend(capture, config)
    provider = FakeEmbeddingProvider(dimensions=8)

    backend.search("retry", provider, 5)

    assert capture.query.scope == config.vector_scope()
    assert capture.query.filters.source_scope == "wiki"
    assert capture.query.filters.profile_name == "semantic"
    assert capture.query.compatibility is not None
    assert capture.query.compatibility.dimensions == 8


def test_wiki_adapter_reconstructs_search_compatibility_after_restart() -> None:
    config = WikiVectorStoreConfig(
        workspace_id="trusted-workspace",
        source_id="trusted-source",
        profile_name="semantic",
        retrieval_cache_state="cache-v1",
        manifest_hash="manifest-v1",
    )
    capture = _CaptureStore()
    backend = WikiPreparedVectorBackend(capture, config)
    provider = FakeEmbeddingProvider(
        provider_id="wiki-provider",
        model_version="wiki-model-v1",
        dimensions=8,
    )

    backend.search("retry", provider, 5)

    assert capture.query.compatibility.provider == "wiki-provider"
    assert capture.query.compatibility.model == "wiki-model-v1"
    assert capture.query.compatibility.manifest_hash == "manifest-v1"


def test_wiki_payload_ignores_untrusted_scope_fields() -> None:
    config = WikiVectorStoreConfig(
        workspace_id="trusted-workspace",
        source_id="trusted-source",
    )
    payload = WikiVectorPayloadAdapter(config).adapt(
        {
            "record_id": "wiki:one",
            "embedding_text": "trusted text",
            "source_scope": "wiki",
            "workspace_id": "attacker-workspace",
            "repository_id": "attacker-source",
        }
    )

    assert payload.workspace_id == "trusted-workspace"
    assert payload.repository_id == "trusted-source"
    assert "workspace_id" not in payload.metadata
    assert "repository_id" not in payload.metadata
