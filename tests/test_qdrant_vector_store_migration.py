from __future__ import annotations

import hashlib
import json

import pytest

from tests.qdrant_test_support import FakeQdrantClient
from worker.retrieval.qdrant_collection_manager import QdrantCollectionManager
from worker.retrieval.qdrant_vector_store import QdrantVectorStore
from worker.retrieval.vector_store_contract import (
    CompatibilitySpec,
    PreparedVectorPoint,
    VectorScope,
    VectorSearchQuery,
)
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
            "distance": "cosine",
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
    raw = _write_source(source)
    client, store, migrator = _migrator()

    plan = migrator.dry_run(source, scope=_scope(), compatibility=_compatibility())

    assert plan.status == "ready"
    assert plan.source_entries == 2
    assert plan.dimensions == 2
    assert plan.distance == "cosine"
    assert plan.scope_fingerprint == hashlib.sha256(
        json.dumps(
            _scope().as_dict(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
    ).hexdigest()
    assert plan.target_collection == (
        store.collection_manager.target_collection_name(
            _scope(),
            index_version=hashlib.sha256(raw).hexdigest(),
        )
    )
    assert client.collections == {}
    assert not any(client.calls.values())


@pytest.mark.parametrize(
    "missing_field",
    [
        "distance",
        "embedding_provider",
        "embedding_model_name",
        "embedding_dimensions",
        "embedding_provider_config_hash",
        "embedding_text_profile",
        "manifest_hash",
        "vector_encoding_profile",
    ],
)
def test_dry_run_blocks_incomplete_compatibility_state_without_writes(
    tmp_path,
    missing_field: str,
) -> None:
    source = tmp_path / "index.json"
    _write_source(source)
    payload = json.loads(source.read_text(encoding="utf-8"))
    payload["state"].pop(missing_field)
    source.write_text(json.dumps(payload), encoding="utf-8")
    client, _, migrator = _migrator()

    plan = migrator.dry_run(
        source,
        scope=_scope(),
        compatibility=_compatibility(),
    )

    assert plan.status == "blocked"
    assert plan.reason == "rebuild_required"
    assert plan.distance == (
        "" if missing_field == "distance" else "cosine"
    )
    assert len(plan.scope_fingerprint) == 64
    assert plan.target_collection is not None
    assert client.collections == {}
    assert not any(client.calls.values())


def test_dry_run_prefers_stored_encoding_config_hash_over_legacy_mode(
    tmp_path,
) -> None:
    source = tmp_path / "index.json"
    _write_source(source)
    payload = json.loads(source.read_text(encoding="utf-8"))
    payload["state"]["vector_encoding_config_hash"] = "encoding-config-hash"
    source.write_text(json.dumps(payload), encoding="utf-8")
    client, _, migrator = _migrator()
    expected = CompatibilitySpec(
        2,
        "cosine",
        "test",
        "v1",
        "default",
        "encoding-config-hash",
        "cfg",
        "vector_store.v1",
        "manifest",
    )

    plan = migrator.dry_run(
        source,
        scope=_scope(),
        compatibility=expected,
    )

    assert plan.status == "ready"
    assert plan.reason == "migration_ready"
    assert not any(client.calls.values())


def test_migration_rejects_mixed_explicit_scopes_without_qdrant_writes(
    tmp_path,
) -> None:
    source = tmp_path / "index.json"
    original = _write_source(source)
    payload = json.loads(original)
    payload["entries"][0].update(_scope().as_dict())
    payload["entries"][1].update(
        VectorScope(
            "other-workspace",
            "repo",
            "default",
            "codecompass",
        ).as_dict()
    )
    source.write_text(json.dumps(payload), encoding="utf-8")
    client, _, migrator = _migrator()

    plan = migrator.dry_run(
        source,
        scope=_scope(),
        compatibility=_compatibility(),
    )
    migrated = migrator.migrate(
        source,
        scope=_scope(),
        compatibility=_compatibility(),
    )

    assert plan.status == "blocked"
    assert plan.reason == "vector_scope_conflict"
    assert plan.compatible_entries == 0
    assert plan.conflicts == ("source_entry_scope_mismatch",)
    assert migrated.result.status == "failed"
    assert migrated.result.reason == "vector_scope_conflict"
    assert migrated.result.accepted == 0
    assert migrated.activated is False
    assert client.aliases == {}
    assert client.collections == {}
    assert not any(client.calls.values())


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
        idempotency_key="migration-pause-resume",
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
        idempotency_key="migration-pause-resume",
    )

    assert completed.result.status == "ok"
    assert completed.activated is True
    assert source.read_bytes() == original
    search = store.search_by_vector(VectorSearchQuery((1.0, 0.0), 10, _scope()))
    assert {hit.record_id for hit in search.hits} == {"a", "b"}


@pytest.mark.parametrize("batch_size", [True, "128", 128.0, 0, 1001])
def test_migration_rejects_invalid_batch_size_before_qdrant_writes(
    tmp_path,
    batch_size: object,
) -> None:
    source = tmp_path / "index.json"
    _write_source(source)
    client, _, migrator = _migrator()

    result = migrator.migrate(
        source,
        scope=_scope(),
        compatibility=_compatibility(),
        batch_size=batch_size,
    )

    assert result.result.reason == "vector_batch_size_invalid"
    assert result.result.accepted == 0
    assert result.result.failed == 2
    assert result.checkpoint is None
    assert client.collections == {}


def test_repeated_migration_is_idempotent(tmp_path) -> None:
    source = tmp_path / "index.json"
    _write_source(source)
    _, _, migrator = _migrator()
    first = migrator.migrate(
        source,
        scope=_scope(),
        compatibility=_compatibility(),
        idempotency_key="migration-repeated",
    )
    second = migrator.migrate(
        source,
        scope=_scope(),
        compatibility=_compatibility(),
        idempotency_key="migration-repeated",
    )
    assert first.result.upserted == 2
    assert second.result.upserted == 0
    assert second.result.skipped == 2


def test_migration_requires_idempotency_before_qdrant_writes(
    tmp_path,
) -> None:
    source = tmp_path / "index.json"
    _write_source(source)
    client, _, migrator = _migrator()

    result = migrator.migrate(
        source,
        scope=_scope(),
        compatibility=_compatibility(),
    )

    assert result.result.status == "failed"
    assert (
        result.result.reason
        == "migration_idempotency_key_required"
    )
    assert result.checkpoint is None
    assert result.activated is False
    assert client.collections == {}


def test_activation_failure_keeps_old_alias_and_json_unchanged_and_can_resume(
    tmp_path,
) -> None:
    source = tmp_path / "index.json"
    original = _write_source(source)
    client, store, migrator = _migrator()
    assert store.rebuild(
        [
            PreparedVectorPoint(
                record_id="old",
                vector=(1.0, 0.0),
                scope=_scope(),
                payload={"kind": "code"},
                source_hash="source-old",
            )
        ],
        compatibility=_compatibility(),
    ).status == "ok"
    alias = store.collection_manager.alias_name(_scope())
    old_collection = client.aliases[alias]
    old_points = dict(client.collections[old_collection]["points"])
    client.fail_next("swap_alias", "qdrant_unavailable")

    interrupted = migrator.migrate(
        source,
        scope=_scope(),
        compatibility=_compatibility(),
        batch_size=1,
        idempotency_key="migration-before-alias",
    )

    assert interrupted.result.status == "partial"
    assert interrupted.result.reason == "qdrant_unavailable"
    assert interrupted.activated is False
    assert interrupted.checkpoint is not None
    assert interrupted.checkpoint.next_offset == 2
    assert client.aliases[alias] == old_collection
    assert client.collections[old_collection]["points"] == old_points
    assert source.read_bytes() == original

    resumed = migrator.migrate(
        source,
        scope=_scope(),
        compatibility=_compatibility(),
        checkpoint=interrupted.checkpoint,
        batch_size=1,
        idempotency_key="migration-before-alias",
    )

    assert resumed.result.status == "ok"
    assert resumed.activated is True
    assert client.aliases[alias] != old_collection
    assert source.read_bytes() == original


def test_incompatible_dimensions_require_rebuild(tmp_path) -> None:
    source = tmp_path / "index.json"
    _write_source(source, dimensions=3)
    _, _, migrator = _migrator()
    plan = migrator.dry_run(source, scope=_scope(), compatibility=_compatibility())
    assert plan.status == "blocked"
    assert plan.reason == "dimensions_mismatch"


def test_checkpoint_is_bound_to_scope_and_idempotency_key(tmp_path) -> None:
    source = tmp_path / "index.json"
    _write_source(source)
    _, _, migrator = _migrator()
    paused = migrator.migrate(
        source,
        scope=_scope(),
        compatibility=_compatibility(),
        batch_size=1,
        max_batches=1,
        idempotency_key="migration-request-a",
    )

    assert paused.checkpoint is not None
    assert len(paused.checkpoint.scope_fingerprint) == 64
    assert len(paused.checkpoint.idempotency_key_hash) == 64

    wrong_key = migrator.migrate(
        source,
        scope=_scope(),
        compatibility=_compatibility(),
        checkpoint=paused.checkpoint,
        batch_size=1,
        idempotency_key="migration-request-b",
    )
    wrong_scope = migrator.migrate(
        source,
        scope=VectorScope(
            "other-workspace",
            "repo",
            "default",
            "codecompass",
        ),
        compatibility=_compatibility(),
        checkpoint=paused.checkpoint,
        batch_size=1,
        idempotency_key="migration-request-a",
    )

    assert wrong_key.result.reason == "migration_checkpoint_invalid"
    assert wrong_scope.result.reason == "migration_checkpoint_invalid"


def test_migration_accepts_prevalidated_bytes_without_reopening_source(tmp_path) -> None:
    source = tmp_path / "index.json"
    raw = _write_source(source)
    source.unlink()
    _, _, migrator = _migrator()

    plan = migrator.dry_run(
        raw,
        scope=_scope(),
        compatibility=_compatibility(),
    )

    assert plan.status == "ready"
    assert plan.source_entries == 2
