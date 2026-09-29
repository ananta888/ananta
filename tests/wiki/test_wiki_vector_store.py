from __future__ import annotations

import pytest

from worker.retrieval.embedding_provider import FakeEmbeddingProvider
from worker.retrieval.vector_store_config import (
    AvailabilityPolicy,
    JsonVectorStoreConfig,
    QdrantEndpointConfig,
    QdrantVectorStoreConfig,
)
from worker.retrieval.vector_store_contract import (
    VectorStoreError,
    VectorStoreFailClosedError,
)
from worker.retrieval.vector_store_factory import VectorStoreFactory
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
        {
            "record_id": "wiki:c1",
            "kind": "wiki_section_chunk",
            "file": "wiki/payment.md",
            "manifest_hash": "mh-wiki-1",
            "embedding_text": "Ananta retry handling",
            "source_scope": "wiki",
        },
        {
            "record_id": "wiki:c2",
            "kind": "wiki_section_chunk",
            "file": "wiki/auth.md",
            "manifest_hash": "mh-wiki-1",
            "embedding_text": "Token verification",
            "source_scope": "wiki",
        },
    ]
    store.rebuild(
        documents=docs,
        embedding_provider=provider,
        retrieval_cache_state="wiki-cache-1",
        manifest_hash="mh-wiki-1",
    )
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


@pytest.mark.parametrize("value", ["false", "true", 0, 1, None])
def test_wiki_qdrant_opt_in_requires_a_real_boolean(value: object) -> None:
    payload = {"qdrant_enabled": value}
    if value is None:
        payload["qdrant_enabled"] = None

    with pytest.raises(ValueError, match="wiki_qdrant_enabled_invalid"):
        WikiVectorStoreConfig.from_mapping(payload)


def test_wiki_vector_config_rejects_unknown_fields() -> None:
    with pytest.raises(ValueError, match="wiki_vector_config_fields_unknown:typo"):
        WikiVectorStoreConfig.from_mapping({"typo": True})


class _UnavailableQdrantStore:
    def __init__(self, reason: str = "qdrant_unavailable") -> None:
        self._reason = reason

    def search_by_vector(self, _query):
        raise VectorStoreError(self._reason)

    def close(self) -> None:
        return None


def _unavailable_qdrant_factory(
    reason: str = "qdrant_unavailable",
) -> VectorStoreFactory:
    return VectorStoreFactory(
        {
            "qdrant": (
                lambda _config, **_kwargs: _UnavailableQdrantStore(
                    reason
                )
            )
        }
    )


def _qdrant_wiki_config(
    *,
    mode: str,
    json_path,
) -> WikiVectorStoreConfig:
    return WikiVectorStoreConfig(
        provider="qdrant",
        qdrant_enabled=True,
        qdrant=QdrantVectorStoreConfig(
            endpoint=QdrantEndpointConfig(),
            collection_prefix="ananta-wiki",
        ),
        retrieval_cache_state="cache-v1",
        manifest_hash="manifest-v1",
        availability=AvailabilityPolicy(mode),
        json=JsonVectorStoreConfig(index_path=json_path),
    )


def test_productive_wiki_store_applies_fail_fast_and_degraded_empty(
    tmp_path,
) -> None:
    provider = FakeEmbeddingProvider(dimensions=6)
    for mode in ("fail_fast", "degraded_empty"):
        store = WikiVectorStore(
            index_path=tmp_path / f"{mode}.json",
            config=_qdrant_wiki_config(
                mode=mode,
                json_path=tmp_path / f"{mode}.json",
            ),
            store_factory=_unavailable_qdrant_factory(),
        )
        if mode == "fail_fast":
            with pytest.raises(
                VectorStoreError,
                match="qdrant_unavailable",
            ):
                store.search(
                    query="retry",
                    embedding_provider=provider,
                )
        else:
            assert (
                store.search(
                    query="retry",
                    embedding_provider=provider,
                )
                == []
            )
        store.close()


@pytest.mark.parametrize(
    "mode",
    ("fail_fast", "degraded_empty", "explicit_json_fallback"),
)
@pytest.mark.parametrize(
    "reason",
    ("qdrant_unauthorized", "incompatible_collection"),
)
def test_productive_wiki_store_never_hides_security_or_schema_failures(
    tmp_path,
    mode: str,
    reason: str,
) -> None:
    path = tmp_path / f"wiki-{mode}-{reason}.json"
    provider = FakeEmbeddingProvider(
        model_version="wiki-fail-closed-v1",
        dimensions=6,
    )
    local = WikiVectorStore(index_path=path)
    local.rebuild(
        documents=[
            {
                "record_id": "wiki:must-not-fallback",
                "kind": "wiki_section_chunk",
                "file": "wiki/unsafe.md",
                "embedding_text": "must not be selected",
                "source_scope": "wiki",
            }
        ],
        embedding_provider=provider,
        retrieval_cache_state="cache-v1",
        manifest_hash="manifest-v1",
    )
    local.close()
    store = WikiVectorStore(
        index_path=path,
        config=_qdrant_wiki_config(
            mode=mode,
            json_path=path,
        ),
        store_factory=_unavailable_qdrant_factory(reason),
    )

    try:
        with pytest.raises(
            VectorStoreFailClosedError,
            match=f"^{reason}$",
        ):
            store.search(
                query="must not fallback",
                embedding_provider=provider,
            )
    finally:
        store.close()


def test_productive_wiki_store_uses_only_compatible_explicit_json_fallback(
    tmp_path,
) -> None:
    path = tmp_path / "wiki-fallback.json"
    provider = FakeEmbeddingProvider(
        model_version="wiki-fallback-v1",
        dimensions=6,
    )
    local = WikiVectorStore(index_path=path)
    local.rebuild(
        documents=[
            {
                "record_id": "wiki:fallback",
                "kind": "wiki_section_chunk",
                "file": "wiki/fallback.md",
                "embedding_text": "retry fallback",
                "source_scope": "wiki",
            }
        ],
        embedding_provider=provider,
        retrieval_cache_state="cache-v1",
        manifest_hash="manifest-v1",
    )
    local.close()
    store = WikiVectorStore(
        index_path=path,
        config=_qdrant_wiki_config(
            mode="explicit_json_fallback",
            json_path=path,
        ),
        store_factory=_unavailable_qdrant_factory(),
    )

    hits = store.search(
        query="retry",
        embedding_provider=provider,
    )

    assert [hit["record_id"] for hit in hits] == ["wiki:fallback"]
    store.close()
