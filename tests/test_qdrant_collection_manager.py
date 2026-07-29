from __future__ import annotations

import pytest

from tests.qdrant_test_support import FakeQdrantClient
from worker.retrieval.qdrant_collection_manager import QdrantCollectionManager
from worker.retrieval.qdrant_collection_schema import (
    QDRANT_BACKEND_SCHEMA_VERSION,
    QdrantSchemaError,
    deterministic_point_id,
    manifest_client_point,
    manifest_point_id,
)
from worker.retrieval.vector_store_contract import CompatibilitySpec, VectorScope


def _scope(repository: str = "repo-a") -> VectorScope:
    return VectorScope("workspace-a", repository, "default", "codecompass")


def _compatibility(**changes) -> CompatibilitySpec:
    values = {
        "dimensions": 3,
        "distance": "cosine",
        "provider": "test",
        "model": "v1",
        "profile": "default",
        "encoding": "float32",
        "config_hash": "cfg",
        "schema_version": "vector_store.v1",
        "manifest_hash": "manifest",
    }
    values.update(changes)
    return CompatibilitySpec(**values)


def test_point_ids_are_deterministic_and_scope_bound() -> None:
    first = deterministic_point_id(_scope(), "record-1")
    assert first == deterministic_point_id(_scope(), "record-1")
    assert first != deterministic_point_id(_scope("repo-b"), "record-1")


def test_manifest_records_the_independent_backend_schema_version() -> None:
    client = FakeQdrantClient()
    manager = QdrantCollectionManager(client)
    collection = manager.create_versioned(
        _scope(),
        _compatibility(),
        index_version="one",
    )

    manifest = manager._manifest_payload(collection)

    assert manifest["backend_schema_version"] == QDRANT_BACKEND_SCHEMA_VERSION
    assert manifest["compatibility"]["schema_version"] == "vector_store.v1"


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"dimensions": 4}, "dimensions_mismatch"),
        ({"distance": "dot"}, "distance_mismatch"),
        ({"provider": "other"}, "provider_changed"),
        ({"model": "v2"}, "model_changed"),
        ({"profile": "other"}, "profile_changed"),
        ({"encoding": "int8"}, "encoding_changed"),
        ({"config_hash": "other"}, "config_changed"),
        ({"schema_version": "vector_store.v2"}, "migration_required"),
        ({"manifest_hash": "other"}, "manifest_changed"),
    ],
)
def test_compatibility_has_stable_reason_codes(changes, reason) -> None:
    client = FakeQdrantClient()
    manager = QdrantCollectionManager(client)
    collection = manager.create_versioned(_scope(), _compatibility(), index_version="one")
    report = manager.compatibility(collection, _compatibility(**changes))
    assert report.compatible is False
    assert report.reason == reason


def test_alias_failure_keeps_previous_collection_active() -> None:
    client = FakeQdrantClient()
    manager = QdrantCollectionManager(client, clock=lambda: 1.0)
    first = manager.create_versioned(_scope(), _compatibility(), index_version="one")
    manager.activate(_scope(), first, _compatibility())
    second = manager.create_versioned(_scope(), _compatibility(), index_version="two")
    client.fail_next("swap_alias", "qdrant_unavailable")

    with pytest.raises(Exception):
        manager.activate(_scope(), second, _compatibility())

    assert manager.active_collection(_scope()) == first


def test_activation_rejects_incomplete_expected_compatibility_before_alias_swap() -> None:
    client = FakeQdrantClient()
    manager = QdrantCollectionManager(client)
    incomplete = CompatibilitySpec(dimensions=3)
    collection = manager.create_versioned(
        _scope(),
        incomplete,
        index_version="incomplete",
    )

    with pytest.raises(QdrantSchemaError, match="rebuild_required"):
        manager.activate(_scope(), collection, incomplete)

    assert client.calls["swap_alias"] == 0
    assert manager.active_collection(_scope()) is None


def test_missing_or_changed_backend_schema_requires_migration_before_activation() -> None:
    client = FakeQdrantClient()
    original_manager = QdrantCollectionManager(client)
    collection = original_manager.create_versioned(
        _scope(),
        _compatibility(),
        index_version="legacy",
    )
    stored_manifest = client.collections[collection]["points"][
        manifest_point_id(collection)
    ]
    stored_manifest.payload.pop("backend_schema_version")

    missing = original_manager.compatibility(collection, _compatibility())
    stored_manifest.payload["backend_schema_version"] = (
        QDRANT_BACKEND_SCHEMA_VERSION
    )
    changed_manager = QdrantCollectionManager(
        client,
        backend_schema_version="qdrant_vector_store.v2",
    )
    changed = changed_manager.compatibility(collection, _compatibility())

    assert missing.reason == "migration_required"
    assert changed.reason == "migration_required"
    with pytest.raises(QdrantSchemaError, match="migration_required"):
        changed_manager.activate(_scope(), collection, _compatibility())
    assert client.calls["swap_alias"] == 0


def test_retention_never_deletes_other_scope_or_active_collection() -> None:
    ticks = iter([1.0, 2.0, 3.0, 4.0])
    client = FakeQdrantClient()
    manager = QdrantCollectionManager(client, clock=lambda: next(ticks))
    old = manager.create_versioned(_scope(), _compatibility(), index_version="old")
    kept = manager.create_versioned(_scope(), _compatibility(), index_version="kept")
    active = manager.create_versioned(_scope(), _compatibility(), index_version="active")
    other = manager.create_versioned(_scope("repo-b"), _compatibility(), index_version="other")
    manager.activate(_scope(), active, _compatibility())

    removed = manager.cleanup_inactive(_scope(), retain=1)

    assert removed == (old,)
    assert set(client.collections) >= {kept, active, other}


def test_staging_collections_are_fresh_and_active_collection_is_never_discarded() -> None:
    tokens = iter(("first", "second"))
    client = FakeQdrantClient()
    manager = QdrantCollectionManager(
        client,
        staging_token_factory=lambda: next(tokens),
    )
    active = manager.create_versioned(
        _scope(),
        _compatibility(),
        index_version="same-version",
    )
    manager.activate(_scope(), active, _compatibility())

    first = manager.create_staging(
        _scope(),
        _compatibility(),
        index_version="same-version",
    )
    second = manager.create_staging(
        _scope(),
        _compatibility(),
        index_version="same-version",
    )

    assert len({active, first, second}) == 3
    assert manager.discard_staging(active, scope=_scope()) is False
    assert active in client.collections
    assert manager.discard_staging(first, scope=_scope()) is True
    assert first not in client.collections


def test_discard_staging_is_fail_closed_when_ownership_cannot_be_verified() -> None:
    client = FakeQdrantClient()
    manager = QdrantCollectionManager(client)
    staging = manager.create_staging(
        _scope(),
        _compatibility(),
        index_version="same-version",
    )
    client.fail_next("retrieve", "qdrant_unavailable")

    assert manager.discard_staging(staging, scope=_scope()) is False
    assert staging in client.collections


def test_activation_rejects_collection_outside_the_scoped_namespace() -> None:
    client = FakeQdrantClient()
    manager = QdrantCollectionManager(client)
    collection = "foreign-collection"
    client.create_collection(collection, dimensions=3, distance="cosine")
    client.upsert(
        collection,
        [
            manifest_client_point(
                collection,
                _scope(),
                _compatibility(),
                created_at_epoch=1.0,
            )
        ],
    )

    with pytest.raises(QdrantSchemaError, match="vector_store_invalid_collection"):
        manager.activate(_scope(), collection, _compatibility())

    assert manager.active_collection(_scope()) is None
