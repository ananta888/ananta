from __future__ import annotations

import json

from tests.qdrant_test_support import FakeQdrantClient
from worker.retrieval.qdrant_collection_manager import QdrantCollectionManager
from worker.retrieval.qdrant_vector_store import QdrantVectorStore
from worker.retrieval.vector_store_contract import CompatibilitySpec, VectorScope, VectorSearchQuery
from worker.retrieval.vector_store_migration import JsonToQdrantMigrator


def _scope() -> VectorScope:
    return VectorScope("workspace", "repo", "default", "codecompass")


def _compatibility() -> CompatibilitySpec:
    return CompatibilitySpec(
        2,
        "cosine",
        "test",
        "v1",
        "default",
        "float32",
        "cfg",
        "vector_store.v1",
        "manifest",
    )


def _write_source(path, *, dimensions=2) -> bytes:
    payload = {
        "state": {
            "schema": "codecompass_vector_index.v2",
            "embedding_provider": "test",
            "embedding_model_name": "v1",
            "embedding_dimensions": dimensions,
            "embedding_provider_config_hash": "cfg",
            "embedding_text_profile": "default",
            "manifest_hash": "manifest",
            "vector_encoding_profile": {"mode": "off"},
        },
        "entries": [
            {"record_id": "a", "vector": [1.0, 0.0], "kind": "code"},
            {"record_id": "b", "vector": [0.0, 1.0], "kind": "code"},
        ],
    }
    raw = json.dumps(payload).encode()
    path.write_bytes(raw)
    return raw


def _migrator():
    client = FakeQdrantClient()
    store = QdrantVectorStore(
        client=client,
        collection_manager=QdrantCollectionManager(client),
    )
    return client, store, JsonToQdrantMigrator(store)


def test_dry_run_is_read_only_and_reports_target_shape(tmp_path) -> None:
    source = tmp_path / "index.json"
    _write_source(source)
    client, _, migrator = _migrator()

    plan = migrator.dry_run(source, scope=_scope(), compatibility=_compatibility())

    assert plan.status == "ready"
    assert plan.source_entries == 2
    assert plan.dimensions == 2
    assert client.collections == {}


def test_migration_can_pause_resume_and_never_deletes_json(tmp_path) -> None:
    source = tmp_path / "index.json"
    original = _write_source(source)
    _, store, migrator = _migrator()

    paused = migrator.migrate(
        source,
        scope=_scope(),
        compatibility=_compatibility(),
        batch_size=1,
        max_batches=1,
    )
    assert paused.result.reason == "migration_paused"
    assert paused.checkpoint is not None
    assert store.collection_manager.active_collection(_scope()) is None

    completed = migrator.migrate(
        source,
        scope=_scope(),
        compatibility=_compatibility(),
        checkpoint=paused.checkpoint,
        batch_size=1,
    )

    assert completed.result.status == "ok"
    assert completed.activated is True
    assert source.read_bytes() == original
    search = store.search_by_vector(VectorSearchQuery((1.0, 0.0), 10, _scope()))
    assert {hit.record_id for hit in search.hits} == {"a", "b"}


def test_repeated_migration_is_idempotent(tmp_path) -> None:
    source = tmp_path / "index.json"
    _write_source(source)
    _, _, migrator = _migrator()
    first = migrator.migrate(source, scope=_scope(), compatibility=_compatibility())
    second = migrator.migrate(source, scope=_scope(), compatibility=_compatibility())
    assert first.result.upserted == 2
    assert second.result.upserted == 0
    assert second.result.skipped == 2


def test_incompatible_dimensions_require_rebuild(tmp_path) -> None:
    source = tmp_path / "index.json"
    _write_source(source, dimensions=3)
    _, _, migrator = _migrator()
    plan = migrator.dry_run(source, scope=_scope(), compatibility=_compatibility())
    assert plan.status == "blocked"
    assert plan.reason == "dimensions_mismatch"
