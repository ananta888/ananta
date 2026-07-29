from __future__ import annotations

import pytest

from tests.qdrant_test_support import FakeQdrantClient
from worker.retrieval.json_vector_store import JsonVectorStore
from worker.retrieval.qdrant_client_port import ClientAvailability
from worker.retrieval.qdrant_collection_manager import (
    QdrantCollectionManager,
)
from worker.retrieval.qdrant_vector_store import QdrantVectorStore
from worker.retrieval.vector_encoding import VectorEncodingProfile
from worker.retrieval.vector_store_config import VectorStoreConfig, VectorStoreConfigError
from worker.retrieval.vector_store_contract import (
    CompatibilitySpec,
    PreparedVectorPoint,
    VectorScope,
    VectorSearchQuery,
)
from worker.retrieval.vector_store_factory import VectorStoreFactory
from worker.retrieval.vector_store_fallback import (
    AvailabilityManagedVectorStore,
)


def test_factory_keeps_json_as_default(tmp_path) -> None:
    config = VectorStoreConfig.for_json(tmp_path / "index.json")
    store = VectorStoreFactory().create(config)

    assert isinstance(store, JsonVectorStore)
    assert store.index_path == tmp_path / "index.json"
    assert store.diagnostics().provider == "json"


def test_config_rejects_unknown_provider_before_factory() -> None:
    with pytest.raises(VectorStoreConfigError, match="unknown_vector_store_provider"):
        VectorStoreConfig.from_mapping({"provider": "unknown"})


def test_factory_registry_is_explicit_and_non_overwriting(tmp_path) -> None:
    sentinel = JsonVectorStore(index_path=tmp_path / "sentinel.json")
    factory = VectorStoreFactory({"json": lambda _config, **_kwargs: sentinel})

    assert factory.create(VectorStoreConfig.for_json(tmp_path / "ignored.json")) is sentinel
    with pytest.raises(VectorStoreConfigError, match="already_registered"):
        factory.register("json", lambda _config, **_kwargs: sentinel)


def test_factory_passes_observer_through_composition_seam(tmp_path) -> None:
    sentinel = JsonVectorStore(index_path=tmp_path / "sentinel.json")
    captured = {}
    observer = object()

    def build(_config, **kwargs):
        captured.update(kwargs)
        return sentinel

    store = VectorStoreFactory({"json": build}).create(
        VectorStoreConfig.for_json(tmp_path / "ignored.json"),
        observer=observer,
    )

    assert store is sentinel
    assert captured["observer"] is observer


def test_factory_composed_qdrant_search_emits_observation() -> None:
    client = FakeQdrantClient()
    events = []

    class Observer:
        def observe(self, observation):
            events.append(observation)

    def build(_config, **kwargs):
        return QdrantVectorStore(
            client=client,
            collection_manager=QdrantCollectionManager(client),
            observer=kwargs["observer"],
        )

    store = VectorStoreFactory({"qdrant": build}).create(
        VectorStoreConfig.from_mapping(
            {"provider": "qdrant", "qdrant": {}}
        ),
        observer=Observer(),
    )
    scope = VectorScope("workspace-a", "repo-a")
    compatibility = CompatibilitySpec(
        dimensions=2,
        provider="test",
        model="v1",
        profile="default",
        encoding=VectorEncodingProfile.disabled().config_hash(),
        config_hash="config-a",
        manifest_hash="manifest-a",
    )
    store.rebuild(
        [
            PreparedVectorPoint(
                record_id="record-a",
                vector=(1.0, 0.0),
                scope=scope,
                payload={"kind": "code"},
                source_hash="source-a",
            )
        ],
        compatibility=compatibility,
    )
    result = store.search_by_vector(
        VectorSearchQuery(
            query_vector=(1.0, 0.0),
            top_k=7,
            scope=scope,
            compatibility=compatibility,
        )
    )

    search_events = [
        event for event in events if event.operation == "search"
    ]
    assert len(search_events) == 1
    assert search_events[0].counts == {
        "top_k": 7,
        "hits": len(result.hits),
    }


def test_factory_composes_explicit_compatible_json_search_fallback(
    tmp_path,
) -> None:
    scope = VectorScope("workspace-a", "repo-a")
    compatibility = CompatibilitySpec(
        dimensions=2,
        provider="test",
        model="v1",
        profile="default",
        encoding=VectorEncodingProfile.disabled().config_hash(),
        config_hash="config-a",
        manifest_hash="manifest-a",
    )
    fallback_path = tmp_path / "fallback.json"
    fallback = JsonVectorStore(index_path=fallback_path)
    fallback.rebuild(
        [
            PreparedVectorPoint(
                record_id="fallback-record",
                vector=(1.0, 0.0),
                scope=scope,
                payload={"kind": "code"},
                source_hash="source-a",
            )
        ],
        compatibility=compatibility,
    )
    fallback.close()
    client = FakeQdrantClient()
    client.availability = ClientAvailability(
        "unavailable",
        "qdrant_unavailable",
    )

    def build(_config, **_kwargs):
        return QdrantVectorStore(
            client=client,
            collection_manager=QdrantCollectionManager(client),
        )

    config = VectorStoreConfig.from_mapping(
        {
            "provider": "qdrant",
            "availability": {
                "on_unavailable": "explicit_json_fallback",
                "fallback_provider": "json",
            },
            "json": {"index_path": str(fallback_path)},
            "qdrant": {},
        }
    )

    store = VectorStoreFactory({"qdrant": build}).create(config)
    result = store.search_by_vector(
        VectorSearchQuery(
            query_vector=(1.0, 0.0),
            top_k=5,
            scope=scope,
            compatibility=compatibility,
        )
    )

    assert isinstance(store, AvailabilityManagedVectorStore)
    assert [hit.record_id for hit in result.hits] == ["fallback-record"]
    assert result.provider_fallback is True
    assert result.requested_provider == "qdrant"
    assert result.effective_provider == "json"
    assert result.reason == "qdrant_unavailable"
    assert result.diagnostics["requested_backend"] == "qdrant"
    assert result.diagnostics["effective_backend"] == "json"
    assert client.calls["query_points"] == 0
