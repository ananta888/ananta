from __future__ import annotations

import pytest

from tests.qdrant_test_support import FakeQdrantClient
from worker.retrieval.qdrant_collection_manager import QdrantCollectionManager
from worker.retrieval.qdrant_collection_schema import (
    QdrantSchemaError,
    deterministic_point_id,
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
        ({"schema_version": "vector_store.v2"}, "schema_mismatch"),
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
