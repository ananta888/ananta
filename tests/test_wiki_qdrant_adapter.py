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
