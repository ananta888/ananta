from __future__ import annotations

import threading
from dataclasses import replace

import pytest

from tests.qdrant_test_support import FakeQdrantClient
from worker.retrieval.qdrant_collection_manager import QdrantCollectionManager
from worker.retrieval.vector_store_contract import CompatibilitySpec, VectorScope


def _scope(workspace: str = "workspace") -> VectorScope:
    return VectorScope(workspace, "repository", "runtime-profile")


def _compatibility() -> CompatibilitySpec:
    return CompatibilitySpec(
        dimensions=3,
        distance="cosine",
        provider="embedding-provider",
        model="embedding-model",
        profile="embedding-profile",
        encoding="float32",
        config_hash="config",
        schema_version="stored-schema.v1",
        manifest_hash="manifest",
    )


def _manager() -> tuple[FakeQdrantClient, QdrantCollectionManager, str]:
    client = FakeQdrantClient()
    manager = QdrantCollectionManager(client)
    collection = manager.create_versioned(
        _scope(),
        _compatibility(),
        index_version="one",
    )
    return client, manager, collection


def test_query_compatibility_requires_an_independent_complete_expected_state() -> None:
    _, manager, collection = _manager()

    missing = manager.query_compatibility(
        collection,
        scope=_scope(),
        dimensions=3,
        distance="cosine",
    )
    incomplete = manager.query_compatibility(
        collection,
        scope=_scope(),
        expected=CompatibilitySpec(dimensions=3),
        dimensions=3,
        distance="cosine",
    )

    assert missing.reason == "vector_store_compatibility_required"
    assert incomplete.reason == "vector_store_compatibility_required"


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("dimensions", 4, "dimensions_mismatch"),
        ("distance", "dot", "distance_mismatch"),
        ("provider", "other-provider", "provider_changed"),
        ("model", "other-model", "model_changed"),
        ("profile", "other-profile", "profile_changed"),
        ("encoding", "int8", "encoding_changed"),
        ("config_hash", "other-config", "config_changed"),
        ("schema_version", "stored-schema.v2", "migration_required"),
        ("manifest_hash", "other-manifest", "manifest_changed"),
    ],
)
def test_query_compatibility_compares_every_expected_field(
    field: str,
    value: object,
    reason: str,
) -> None:
    _, manager, collection = _manager()

    report = manager.query_compatibility(
        collection,
        scope=_scope(),
        expected=replace(_compatibility(), **{field: value}),
        dimensions=4 if field == "dimensions" else 3,
        distance="dot" if field == "distance" else "cosine",
    )

    assert report.compatible is False
    assert report.reason == reason


def test_query_compatibility_checks_scope_and_physical_collection_shape() -> None:
    client, manager, collection = _manager()

    compatible = manager.query_compatibility(
        collection,
        scope=_scope(),
        expected=_compatibility(),
        dimensions=3,
        distance="cosine",
    )
    scope_mismatch = manager.query_compatibility(
        collection,
        scope=_scope("other"),
        expected=_compatibility(),
        dimensions=3,
        distance="cosine",
    )
    client.collections[collection]["dimensions"] = 4
    physical_mismatch = manager.query_compatibility(
        collection,
        scope=_scope(),
        expected=_compatibility(),
        dimensions=3,
        distance="cosine",
    )

    assert compatible.compatible is True
    assert scope_mismatch.reason == "vector_scope_conflict"
    assert physical_mismatch.reason == "dimensions_mismatch"


def test_query_compatibility_reuses_scope_manifest_for_shape_validation() -> None:
    client, manager, collection = _manager()
    retrieve_before = client.calls["retrieve"]
    info_before = client.calls["collection_info"]

    report = manager.query_compatibility(
        collection,
        scope=_scope(),
        expected=_compatibility(),
        dimensions=3,
        distance="cosine",
    )

    assert report.compatible is True
    assert client.calls["retrieve"] - retrieve_before == 1
    assert client.calls["collection_info"] - info_before == 1


def test_query_compatibility_reads_manifest_and_physical_shape_concurrently() -> None:
    client, manager, collection = _manager()
    barrier = threading.Barrier(2)
    original_retrieve = client.retrieve
    original_collection_info = client.collection_info

    def retrieve(*args, **kwargs):
        barrier.wait(timeout=1)
        return original_retrieve(*args, **kwargs)

    def collection_info(*args, **kwargs):
        barrier.wait(timeout=1)
        return original_collection_info(*args, **kwargs)

    client.retrieve = retrieve
    client.collection_info = collection_info

    report = manager.query_compatibility(
        collection,
        scope=_scope(),
        expected=_compatibility(),
        dimensions=3,
        distance="cosine",
    )

    assert report.compatible is True
