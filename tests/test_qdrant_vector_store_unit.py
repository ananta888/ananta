from __future__ import annotations

import threading
from dataclasses import dataclass, replace

import pytest

from tests.qdrant_test_support import FakeQdrantClient
from worker.retrieval.json_vector_store import JsonVectorStore
from worker.retrieval.qdrant_client_port import ClientAvailability, ClientPoint
from worker.retrieval.qdrant_collection_manager import QdrantCollectionManager
from worker.retrieval.qdrant_collection_schema import (
    deterministic_point_id,
    manifest_point_id,
)
from worker.retrieval.qdrant_vector_store import QdrantVectorStore
from worker.retrieval.vector_store_config import AvailabilityPolicy
from worker.retrieval.vector_store_contract import (
    CompatibilitySpec,
    PreparedVectorPoint,
    VectorIndexWritePlan,
    VectorScope,
    VectorSearchHit,
    VectorSearchQuery,
    VectorSearchResult,
    VectorStore,
    VectorStoreFailClosedError,
    VectorStoreFilters,
)
from worker.retrieval.vector_store_fallback import (
    AvailabilityManagedVectorStore,
    ClientAvailabilityProbe,
    FallbackVectorSearch,
)


def _scope(workspace: str = "workspace-a") -> VectorScope:
    return VectorScope(workspace, "repo-a", "default", "codecompass")


def _compatibility() -> CompatibilitySpec:
    return CompatibilitySpec(
        3,
        "cosine",
        "test",
        "v1",
        "default",
        "float32",
        "cfg",
        "vector_store.v1",
        "manifest-a",
    )


def _point(record_id: str, vector, *, scope=None, source_hash=None, kind="function") -> PreparedVectorPoint:
    return PreparedVectorPoint(
        record_id,
        tuple(vector),
        scope or _scope(),
        {
            "kind": kind,
            "file": f"src/{record_id}.py",
            "source_scope": "repo",
            "role_labels": ["code"],
        },
        source_hash or f"hash-{record_id}",
    )


def _store(
    *,
    client=None,
    manager=None,
    observer=None,
    retention_collections=2,
    store_embedding_text=False,
):
    client = client or FakeQdrantClient()
    manager = manager or QdrantCollectionManager(client)
    return client, QdrantVectorStore(
        client=client,
        collection_manager=manager,
        observer=observer,
        retention_collections=retention_collections,
        store_embedding_text=store_embedding_text,
    )


def test_empty_collection_returns_empty_result_without_mutation() -> None:
    client, store = _store()
    result = store.search_by_vector(VectorSearchQuery((1.0, 0.0, 0.0), 10, _scope()))
    assert result.hits == ()
    assert result.reason == "empty_collection"
    assert client.calls["create_collection"] == 0


def test_rebuild_searches_with_server_filter_and_maps_score() -> None:
    client, store = _store()
    assert store.rebuild(
        [
            _point("best", (1.0, 0.0, 0.0)),
            _point("other", (0.0, 1.0, 0.0), kind="class"),
        ],
        compatibility=_compatibility(),
    ).status == "ok"

    result = store.search_by_vector(
        VectorSearchQuery(
            (1.0, 0.0, 0.0),
            10,
            _scope(),
            VectorStoreFilters(kinds=("function",), file_prefix="src"),
        )
    )

    assert [hit.record_id for hit in result.hits] == ["best"]
    assert result.hits[0].score == 1.0
    assert client.calls["query_points"] == 1


@pytest.mark.parametrize(
    "reason",
    ["qdrant_timeout", "qdrant_unauthorized", "collection_missing"],
)
def test_search_maps_client_failures_without_mutating_the_index(
    reason: str,
) -> None:
    client, store = _store()
    assert store.rebuild(
        [_point("one", (1.0, 0.0, 0.0))],
        compatibility=_compatibility(),
    ).status == "ok"
    mutation_operations = (
        "create_collection",
        "delete_collection",
        "upsert",
        "delete_points",
        "delete_by_filter",
        "swap_alias",
    )
    mutations_before = {
        operation: client.calls[operation]
        for operation in mutation_operations
    }
    query_calls_before = client.calls["query_points"]
    client.fail_next("query_points", reason)

    result = store.search_by_vector(
        VectorSearchQuery(
            (1.0, 0.0, 0.0),
            10,
            _scope(),
            compatibility=_compatibility(),
        )
    )

    assert result.hits == ()
    assert result.reason == reason
    assert result.diagnostics == {"status": "degraded", "reason": reason}
    assert client.calls["query_points"] == query_calls_before + 1
    assert {
        operation: client.calls[operation]
        for operation in mutation_operations
    } == mutations_before


def test_upsert_skips_unchanged_source_hash_and_is_idempotent() -> None:
    client, store = _store()
    point = _point("one", (1.0, 0.0, 0.0))
    store.rebuild([point], compatibility=_compatibility())
    upsert_calls = client.calls["upsert"]

    result = store.upsert([point])

    assert result.accepted == 1
    assert result.upserted == 0
    assert result.skipped == 1
    assert result.failed == 0
    assert client.calls["upsert"] == upsert_calls


def test_direct_collection_upsert_rejects_cross_scope_points() -> None:
    client, store = _store()
    collection = store.prepare_collection(
        _scope("workspace-a"),
        _compatibility(),
        index_version="cross-scope-guard",
    )
    upsert_calls = client.calls["upsert"]

    result = store.upsert_to_collection(
        collection,
        [
            _point(
                "foreign",
                (1.0, 0.0, 0.0),
                scope=_scope("workspace-b"),
            )
        ],
        _compatibility(),
    )

    assert result.status == "failed"
    assert result.reason == "vector_scope_conflict"
    assert result.accepted == 0
    assert result.failed == 1
    assert client.calls["upsert"] == upsert_calls


@pytest.mark.parametrize("batch_size", [True, "128", 128.0])
def test_upsert_rejects_non_integer_batch_size_without_network_write(
    batch_size: object,
) -> None:
    client, store = _store()
    point = _point("one", (1.0, 0.0, 0.0))
    store.rebuild([point], compatibility=_compatibility())
    upsert_calls = client.calls["upsert"]

    result = store.upsert([point], batch_size=batch_size)

    assert result.reason == "vector_batch_size_invalid"
    assert result.accepted == 0
    assert result.failed == 1
    assert client.calls["upsert"] == upsert_calls


@pytest.mark.parametrize(
    ("batch_size", "expected_calls"),
    [(1, 3), (1000, 1)],
)
def test_upsert_honors_inclusive_batch_boundaries(
    batch_size: int,
    expected_calls: int,
) -> None:
    client, store = _store()
    store.rebuild(
        [_point("seed", (1.0, 0.0, 0.0))],
        compatibility=_compatibility(),
    )
    upsert_calls = client.calls["upsert"]
    points = [
        _point(f"new-{index}", (1.0, 0.0, 0.0))
        for index in range(3)
    ]

    result = store.upsert(points, batch_size=batch_size)

    assert result.accepted == 3
    assert result.upserted == 3
    assert result.failed == 0
    assert client.calls["upsert"] - upsert_calls == expected_calls


def test_planned_rebuild_uses_the_requested_qdrant_batch_size() -> None:
    client, store = _store()
    points = [
        _point(f"record-{index}", (1.0, 0.0, 0.0))
        for index in range(3)
    ]

    result = store.rebuild_with_plan(
        points,
        compatibility=_compatibility(),
        plan=VectorIndexWritePlan(batch_size=1),
    )

    assert result.status == "ok"
    assert result.diagnostics["batch_size"] == 1
    assert client.calls["upsert"] == 4  # one manifest plus three data batches


def test_planned_refresh_uses_the_requested_qdrant_batch_size() -> None:
    client, store = _store()
    points = [
        _point(f"record-{index}", (1.0, 0.0, 0.0))
        for index in range(3)
    ]
    store.rebuild(points, compatibility=_compatibility())
    changed = [
        replace(point, source_hash=f"changed-{point.record_id}")
        for point in points
    ]
    upsert_calls = client.calls["upsert"]

    result = store.refresh_with_plan(
        changed,
        compatibility=_compatibility(),
        plan=VectorIndexWritePlan(batch_size=1),
    )

    assert result.status == "ok"
    assert result.diagnostics["batch_size"] == 1
    assert client.calls["upsert"] - upsert_calls == 3


def test_upsert_rejects_batch_size_above_maximum_without_network_write() -> None:
    client, store = _store()
    point = _point("one", (1.0, 0.0, 0.0))
    store.rebuild([point], compatibility=_compatibility())
    upsert_calls = client.calls["upsert"]

    result = store.upsert([point], batch_size=1001)

    assert result.reason == "vector_batch_size_invalid"
    assert result.accepted == 0
    assert result.failed == 1
    assert client.calls["upsert"] == upsert_calls


def test_multi_batch_partial_failure_reports_all_bounded_counts() -> None:
    client, store = _store()
    store.rebuild(
        [_point("seed", (1.0, 0.0, 0.0))],
        compatibility=_compatibility(),
    )
    points = [
        _point(f"new-{index}", (1.0, 0.0, 0.0))
        for index in range(129)
    ]
    client.fail_on_nth_next("upsert", 2, "qdrant_timeout")

    result = store.upsert(points, batch_size=128)

    assert result.status == "partial"
    assert result.reason == "qdrant_timeout"
    assert result.accepted == 129
    assert result.upserted == 128
    assert result.skipped == 0
    assert result.failed == 1
    assert result.diagnostics["errors"] == ("qdrant_timeout",)
    assert result.diagnostics["failure_batches"] == (
        {"batch_index": 1, "reason_code": "qdrant_timeout"},
    )
    assert result.diagnostics["total_failure_batches"] == 1
    assert result.diagnostics["failure_batches_truncated"] is False


def test_multi_batch_failures_have_bounded_batch_diagnostics() -> None:
    client, store = _store()
    store.rebuild(
        [_point("seed", (1.0, 0.0, 0.0))],
        compatibility=_compatibility(),
    )
    points = [
        _point(f"new-{index}", (1.0, 0.0, 0.0))
        for index in range(34)
    ]
    for _ in points:
        client.fail_next("upsert", "qdrant_timeout")

    result = store.upsert(points, batch_size=1)

    assert result.status == "failed"
    assert result.accepted == 34
    assert result.failed == 34
    assert result.diagnostics["total_failure_batches"] == 34
    assert len(result.diagnostics["failure_batches"]) == 32
    assert result.diagnostics["failure_batches"][0] == {
        "batch_index": 0,
        "reason_code": "qdrant_timeout",
    }
    assert result.diagnostics["failure_batches"][-1] == {
        "batch_index": 31,
        "reason_code": "qdrant_timeout",
    }
    assert result.diagnostics["failure_batches_truncated"] is True


@pytest.mark.parametrize("operation", ["upsert", "rebuild"])
def test_mismatched_supplied_point_id_fails_before_network_writes(
    operation: str,
) -> None:
    client, store = _store()
    point = replace(
        _point("record", (1.0, 0.0, 0.0)),
        point_id="00000000-0000-0000-0000-000000000000",
    )

    result = (
        store.upsert([point])
        if operation == "upsert"
        else store.rebuild([point], compatibility=_compatibility())
    )

    assert result.status == "failed"
    assert result.reason == "vector_point_id_mismatch"
    assert result.accepted == 0
    assert client.calls["create_collection"] == 0
    assert client.calls["upsert"] == 0
    assert client.calls["swap_alias"] == 0


def test_failed_rebuild_keeps_active_collection() -> None:
    client, store = _store()
    store.rebuild([_point("old", (1.0, 0.0, 0.0))], compatibility=_compatibility())
    old_collection = store.collection_manager.active_collection(_scope())
    client.fail_next("upsert", "qdrant_unavailable")

    result = store.rebuild(
        [_point("new", (0.0, 1.0, 0.0))],
        compatibility=replace(_compatibility(), manifest_hash="manifest-b"),
    )

    assert result.status == "failed"
    assert store.collection_manager.active_collection(_scope()) == old_collection


def test_invalid_payload_rebuild_is_bounded_and_discards_staging() -> None:
    client, store = _store()
    store.rebuild(
        [_point("old", (1.0, 0.0, 0.0))],
        compatibility=_compatibility(),
    )
    old_collection = store.collection_manager.active_collection(_scope())
    invalid = replace(
        _point("invalid", (1.0, 0.0, 0.0)),
        payload={"kind": "code", "importance_score": "not-a-number"},
    )

    result = store.rebuild([invalid], compatibility=_compatibility())

    assert result.status == "failed"
    assert result.reason == "vector_payload_invalid"
    assert result.accepted == 1
    assert store.collection_manager.active_collection(_scope()) == old_collection
    assert set(client.collections) == {old_collection}


@pytest.mark.parametrize(
    ("operation", "nth_call"),
    [
        ("create_collection", 1),
        ("upsert", 1),
        ("upsert", 2),
        ("collection_info", 2),
        ("swap_alias", 1),
    ],
)
def test_rebuild_failure_injection_never_mutates_or_deletes_active_collection(
    operation: str,
    nth_call: int,
) -> None:
    client, store = _store()
    old_point = _point("old", (1.0, 0.0, 0.0))
    assert store.rebuild([old_point], compatibility=_compatibility()).status == "ok"
    old_collection = store.collection_manager.active_collection(_scope())
    old_points = dict(client.collections[old_collection]["points"])
    client.fail_on_nth_next(operation, nth_call, "qdrant_unavailable")

    result = store.rebuild(
        [_point("new", (0.0, 1.0, 0.0))],
        compatibility=_compatibility(),
    )

    assert result.status == "failed"
    assert store.collection_manager.active_collection(_scope()) == old_collection
    assert old_collection in client.collections
    assert client.collections[old_collection]["points"] == old_points
    assert set(client.collections) == {old_collection}


def test_cleanup_failure_after_alias_swap_does_not_turn_committed_rebuild_into_failure() -> None:
    client, store = _store(retention_collections=1)
    for record_id in ("one", "two"):
        assert store.rebuild(
            [_point(record_id, (1.0, 0.0, 0.0))],
            compatibility=_compatibility(),
        ).status == "ok"
    previous = store.collection_manager.active_collection(_scope())
    client.fail_next("delete_collection", "qdrant_unavailable")

    result = store.rebuild(
        [_point("three", (0.0, 1.0, 0.0))],
        compatibility=_compatibility(),
    )

    assert result.status == "ok"
    assert result.diagnostics["cleanup_reason"] == "qdrant_unavailable"
    assert store.collection_manager.active_collection(_scope()) != previous


class _BlockingSwapClient(FakeQdrantClient):
    def __init__(self) -> None:
        super().__init__()
        self.block_swap = False
        self.swap_started = threading.Event()
        self.allow_swap = threading.Event()

    def swap_alias(self, alias_name: str, collection_name: str) -> None:
        if self.block_swap:
            self.swap_started.set()
            assert self.allow_swap.wait(timeout=5)
        super().swap_alias(alias_name, collection_name)


def test_concurrent_reader_sees_only_complete_old_or_new_collection() -> None:
    client = _BlockingSwapClient()
    _, store = _store(client=client)
    assert store.rebuild(
        [_point("old", (1.0, 0.0, 0.0))],
        compatibility=_compatibility(),
    ).status == "ok"
    client.block_swap = True
    results = []
    writer = threading.Thread(
        target=lambda: results.append(
            store.rebuild(
                [_point("new", (1.0, 0.0, 0.0))],
                compatibility=_compatibility(),
            )
        )
    )
    writer.start()
    assert client.swap_started.wait(timeout=5)

    during = store.search_by_vector(
        VectorSearchQuery((1.0, 0.0, 0.0), 10, _scope())
    )
    client.allow_swap.set()
    writer.join(timeout=5)
    after = store.search_by_vector(
        VectorSearchQuery((1.0, 0.0, 0.0), 10, _scope())
    )

    assert not writer.is_alive()
    assert [hit.record_id for hit in during.hits] == ["old"]
    assert [hit.record_id for hit in after.hits] == ["new"]
    assert results[0].status == "ok"


def test_delete_is_scope_checked_and_rename_is_upsert_then_delete() -> None:
    _, store = _store()
    old = _point("old", (1.0, 0.0, 0.0))
    store.rebuild([old], compatibility=_compatibility())
    renamed = _point("new", (1.0, 0.0, 0.0))

    result = store.rename("old", renamed)
    search = store.search_by_vector(VectorSearchQuery((1.0, 0.0, 0.0), 10, _scope()))

    assert result.status == "ok"
    assert [hit.record_id for hit in search.hits] == ["new"]


def test_delete_reports_scope_partial_counts_and_repetition_is_idempotent() -> None:
    client, store = _store()
    assert store.rebuild(
        [_point("safe", (1.0, 0.0, 0.0))],
        compatibility=_compatibility(),
    ).status == "ok"
    active = store.collection_manager.active_collection(_scope())
    assert active is not None
    foreign_id = deterministic_point_id(_scope(), "foreign")
    client.collections[active]["points"][foreign_id] = ClientPoint(
        point_id=foreign_id,
        vector=(1.0, 0.0, 0.0),
        payload={
            "record_id": "foreign",
            "workspace_id": "other-workspace",
            "repository_id": _scope().repository_id,
            "profile_name": _scope().profile_name,
            "domain": _scope().domain,
            "_ananta_record_type": "record",
        },
    )

    first = store.delete(["safe", "foreign"], scope=_scope())
    repeated = store.delete(["safe", "foreign"], scope=_scope())

    assert first.status == "partial"
    assert first.reason == "vector_scope_conflict"
    assert (first.accepted, first.deleted, first.failed) == (2, 1, 1)
    assert repeated.status == "partial"
    assert (repeated.accepted, repeated.deleted, repeated.failed) == (2, 0, 1)
    assert foreign_id in client.collections[active]["points"]


def test_delete_can_retry_transient_failure_and_remains_idempotent() -> None:
    client, store = _store()
    assert store.rebuild(
        [
            _point("one", (1.0, 0.0, 0.0)),
            _point("two", (0.0, 1.0, 0.0)),
        ],
        compatibility=_compatibility(),
    ).status == "ok"
    client.fail_next("delete_points", "qdrant_timeout")

    failed = store.delete(["one", "two"], scope=_scope())
    retried = store.delete(["one", "two"], scope=_scope())
    repeated = store.delete(["one", "two"], scope=_scope())

    assert failed.status == "failed"
    assert failed.reason == "qdrant_timeout"
    assert (failed.accepted, failed.deleted, failed.failed) == (2, 0, 2)
    assert retried.status == "ok"
    assert (retried.accepted, retried.deleted, retried.failed) == (2, 2, 0)
    assert repeated.status == "ok"
    assert (repeated.accepted, repeated.deleted, repeated.failed) == (2, 0, 0)


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("dimensions", 4, "dimensions_mismatch"),
        ("distance", "dot", "distance_mismatch"),
        ("provider", "other", "provider_changed"),
        ("model", "other", "model_changed"),
        ("profile", "other", "profile_changed"),
        ("encoding", "int8", "encoding_changed"),
        ("config_hash", "other", "config_changed"),
        ("schema_version", "vector_store.v2", "migration_required"),
        ("manifest_hash", "other", "manifest_changed"),
    ],
)
def test_search_fails_closed_for_every_compatibility_mismatch(
    field: str,
    value: object,
    reason: str,
) -> None:
    client, store = _store()
    store.rebuild(
        [_point("one", (1.0, 0.0, 0.0))],
        compatibility=_compatibility(),
    )
    calls_before = client.calls["query_points"]
    expected = replace(_compatibility(), **{field: value})

    result = store.search_by_vector(
        VectorSearchQuery(
            (1.0, 0.0, 0.0),
            10,
            _scope(),
            compatibility=expected,
        )
    )

    assert result.reason == reason
    assert result.hits == ()
    assert set(result.diagnostics["compatibility"]) == {"expected", "found"}
    assert "scope" not in result.diagnostics["compatibility"]["expected"]
    assert "vector" not in result.diagnostics["compatibility"]["found"]
    assert client.calls["query_points"] == calls_before


def test_search_after_restart_requires_independent_compatibility() -> None:
    client, store = _store()
    store.rebuild(
        [_point("one", (1.0, 0.0, 0.0))],
        compatibility=_compatibility(),
    )
    restarted = QdrantVectorStore(
        client=client,
        collection_manager=store.collection_manager,
    )

    missing = restarted.search_by_vector(
        VectorSearchQuery((1.0, 0.0, 0.0), 10, _scope())
    )
    verified = restarted.search_by_vector(
        VectorSearchQuery(
            (1.0, 0.0, 0.0),
            10,
            _scope(),
            compatibility=_compatibility(),
        )
    )

    assert missing.reason == "vector_store_compatibility_required"
    assert missing.hits == ()
    assert [hit.record_id for hit in verified.hits] == ["one"]


def test_search_rejects_backend_schema_mismatch_without_querying_points() -> None:
    client, store = _store()
    store.rebuild(
        [_point("one", (1.0, 0.0, 0.0))],
        compatibility=_compatibility(),
    )
    collection = store.collection_manager.active_collection(_scope())
    manifest = client.collections[collection]["points"][
        manifest_point_id(collection)
    ]
    manifest.payload["backend_schema_version"] = "qdrant_vector_store.v2"
    calls_before = client.calls["query_points"]

    result = store.search_by_vector(
        VectorSearchQuery(
            (1.0, 0.0, 0.0),
            10,
            _scope(),
            compatibility=_compatibility(),
        )
    )

    assert result.reason == "migration_required"
    assert result.hits == ()
    assert result.diagnostics["compatibility"]["expected"][
        "backend_schema_version"
    ] == "qdrant_vector_store.v1"
    assert result.diagnostics["compatibility"]["found"][
        "backend_schema_version"
    ] == "qdrant_vector_store.v2"
    assert client.calls["query_points"] == calls_before


def test_role_label_filters_have_the_same_all_labels_semantics_as_json(
    tmp_path,
) -> None:
    scope = _scope()
    points = (
        replace(
            _point("reader", (1.0, 0.0, 0.0)),
            payload={"role_labels": ["reader"], "source_scope": "repo"},
        ),
        replace(
            _point("admin", (1.0, 0.0, 0.0)),
            payload={
                "role_labels": ["reader", "admin"],
                "source_scope": "repo",
            },
        ),
    )
    _, qdrant = _store()
    json_store = JsonVectorStore(index_path=tmp_path / "vectors.json")
    qdrant.rebuild(points, compatibility=_compatibility())
    json_store.rebuild(points, compatibility=_compatibility())
    query = VectorSearchQuery(
        (1.0, 0.0, 0.0),
        10,
        scope,
        VectorStoreFilters(role_labels=("reader", "admin")),
    )

    qdrant_result = qdrant.search_by_vector(query)
    json_result = json_store.search_by_vector(query)

    assert [hit.record_id for hit in qdrant_result.hits] == ["admin"]
    assert [hit.record_id for hit in json_result.hits] == ["admin"]


def test_empty_existing_source_hash_is_rewritten_and_never_skipped() -> None:
    client, store = _store()
    point = _point("one", (1.0, 0.0, 0.0))
    store.rebuild([point], compatibility=_compatibility())
    active = store.collection_manager.active_collection(_scope())
    record = next(
        item
        for item in client.collections[active]["points"].values()
        if item.payload.get("_ananta_record_type") == "record"
    )
    client.collections[active]["points"][record.point_id] = replace(
        record,
        payload={**dict(record.payload), "source_hash": ""},
    )

    result = store.upsert([point])

    assert result.upserted == 1
    assert result.skipped == 0


def test_prepared_point_requires_source_hash() -> None:
    with pytest.raises(ValueError, match="missing_source_hash"):
        PreparedVectorPoint(
            "record",
            (1.0, 0.0, 0.0),
            _scope(),
            {"kind": "code"},
        )


def test_embedding_text_is_stored_only_after_explicit_opt_in() -> None:
    point = replace(
        _point("one", (1.0, 0.0, 0.0)),
        payload={"kind": "code", "embedding_text": "sensitive source text"},
    )
    default_client, default_store = _store()
    opted_client, opted_store = _store(store_embedding_text=True)

    default_store.rebuild([point], compatibility=_compatibility())
    opted_store.rebuild([point], compatibility=_compatibility())

    def record_payload(client, store):
        active = store.collection_manager.active_collection(_scope())
        return next(
            item.payload
            for item in client.collections[active]["points"].values()
            if item.payload.get("_ananta_record_type") == "record"
        )

    assert "embedding_text" not in record_payload(default_client, default_store)
    assert record_payload(opted_client, opted_store)["embedding_text"] == (
        "sensitive source text"
    )


def test_search_returns_embedding_text_only_for_the_active_explicit_opt_in() -> None:
    point = replace(
        _point("one", (1.0, 0.0, 0.0)),
        payload={"kind": "code", "embedding_text": "searchable excerpt"},
    )
    client, opted_store = _store(store_embedding_text=True)
    opted_store.rebuild([point], compatibility=_compatibility())
    default_reader = QdrantVectorStore(
        client=client,
        collection_manager=opted_store.collection_manager,
        store_embedding_text=False,
    )
    query = VectorSearchQuery(
        (1.0, 0.0, 0.0),
        1,
        _scope(),
        compatibility=_compatibility(),
    )

    opted_hit = opted_store.search_by_vector(query).hits[0]
    redacted_hit = default_reader.search_by_vector(query).hits[0]

    assert opted_hit.payload["embedding_text"] == "searchable excerpt"
    assert "embedding_text" not in redacted_hit.payload


def test_embedding_text_policy_change_updates_same_source_hash() -> None:
    point = replace(
        _point("one", (1.0, 0.0, 0.0)),
        payload={"kind": "code", "embedding_text": "sensitive source text"},
    )
    client, default_store = _store()
    default_store.rebuild([point], compatibility=_compatibility())
    opted_store = QdrantVectorStore(
        client=client,
        collection_manager=default_store.collection_manager,
        store_embedding_text=True,
    )

    added = opted_store.refresh([point], compatibility=_compatibility())
    active = opted_store.collection_manager.active_collection(_scope())
    stored = next(
        item
        for item in client.collections[active]["points"].values()
        if item.payload.get("_ananta_record_type") == "record"
    )
    removed = default_store.refresh([point], compatibility=_compatibility())
    redacted = client.collections[active]["points"][stored.point_id]

    assert added.upserted == 1
    assert stored.payload["embedding_text"] == "sensitive source text"
    assert removed.upserted == 1
    assert "embedding_text" not in redacted.payload


class _CollectingObserver:
    def __init__(self) -> None:
        self.events = []

    def observe(self, observation) -> None:
        self.events.append(observation)


class _FailingObserver:
    def observe(self, observation) -> None:
        del observation
        raise RuntimeError("metrics unavailable")


def test_store_emits_observations_and_observer_failure_is_non_fatal() -> None:
    observer = _CollectingObserver()
    _, observed_store = _store(observer=observer)
    result = observed_store.rebuild(
        [_point("one", (1.0, 0.0, 0.0))],
        compatibility=_compatibility(),
    )
    searched = observed_store.search_by_vector(
        VectorSearchQuery((1.0, 0.0, 0.0), 7, _scope())
    )
    _, resilient_store = _store(observer=_FailingObserver())
    resilient = resilient_store.rebuild(
        [_point("two", (1.0, 0.0, 0.0))],
        compatibility=_compatibility(),
    )

    assert result.status == "ok"
    assert result.accepted == 1
    assert resilient.status == "ok"
    rebuild_event = next(
        event for event in observer.events if event.operation == "rebuild"
    )
    search_event = next(
        event for event in observer.events if event.operation == "search"
    )
    assert rebuild_event.counts["accepted"] == 1
    assert rebuild_event.counts["upserted"] == 1
    assert search_event.counts == {"top_k": 7, "hits": len(searched.hits)}


def test_availability_decorator_preserves_delete_scope_writer_capability(
    tmp_path,
) -> None:
    primary = JsonVectorStore(index_path=tmp_path / "primary.json")
    primary.rebuild(
        [_point("one", (1.0, 0.0, 0.0))],
        compatibility=_compatibility(),
    )
    search = FallbackVectorSearch(
        primary=primary,
        fallback=None,
        policy=AvailabilityPolicy(),
    )
    managed = AvailabilityManagedVectorStore(primary=primary, search=search)

    assert isinstance(managed, VectorStore)
    assert managed.delete_scope(_scope()).deleted == 1


@dataclass
class _StaticSearch:
    result: VectorSearchResult

    def search_by_vector(self, query):
        del query
        return self.result


class _ForbiddenSearch:
    def search_by_vector(self, query):
        del query
        raise AssertionError("fail-closed availability state reached a store")


@pytest.mark.parametrize(
    ("availability", "expected_status"),
    [
        (
            ClientAvailability("unavailable", "qdrant_unauthorized"),
            "unauthorized",
        ),
        (
            ClientAvailability("degraded", "incompatible_collection"),
            "incompatible_collection",
        ),
    ],
)
def test_client_availability_probe_normalises_security_and_schema_states(
    availability: ClientAvailability,
    expected_status: str,
) -> None:
    client = FakeQdrantClient()
    client.availability = availability

    state = ClientAvailabilityProbe(client).probe()

    assert state.status == expected_status
    assert state.reason == availability.reason


@pytest.mark.parametrize(
    "mode",
    ("fail_fast", "degraded_empty", "explicit_json_fallback"),
)
@pytest.mark.parametrize(
    ("availability", "expected_reason", "expected_status"),
    (
        (
            ClientAvailability("unavailable", "qdrant_unauthorized"),
            "qdrant_unauthorized",
            "unauthorized",
        ),
        (
            ClientAvailability("degraded", "incompatible_collection"),
            "incompatible_collection",
            "incompatible_collection",
        ),
        (
            ClientAvailability(
                "degraded",
                "vector_store_compatibility_required",
            ),
            "vector_store_compatibility_required",
            "incompatible_collection",
        ),
    ),
)
def test_security_and_schema_probe_states_always_fail_closed(
    mode: str,
    availability: ClientAvailability,
    expected_reason: str,
    expected_status: str,
) -> None:
    client = FakeQdrantClient()
    client.availability = availability
    forbidden = _ForbiddenSearch()
    observer = _CollectingObserver()
    port = FallbackVectorSearch(
        primary=forbidden,
        fallback=forbidden,
        policy=AvailabilityPolicy(mode),
        availability_probe=ClientAvailabilityProbe(client),
        fallback_compatibility=lambda _query: True,
        observer=observer,
    )

    with pytest.raises(
        VectorStoreFailClosedError,
        match=f"^{expected_reason}$",
    ) as captured:
        port.search_by_vector(
            VectorSearchQuery((1.0,), 1, _scope())
        )

    assert captured.value.reason == expected_reason
    assert captured.value.details == {
        "requested_backend": "qdrant",
        "effective_backend": "qdrant",
        "provider_fallback": False,
        "availability_status": expected_status,
    }
    assert len(observer.events) == 1
    assert observer.events[0].outcome == "failed"
    assert observer.events[0].provider_fallback is False


@pytest.mark.parametrize(
    "reason",
    (
        "qdrant_unauthorized",
        "incompatible_collection",
        "vector_store_compatibility_required",
    ),
)
def test_security_and_schema_primary_results_cannot_select_json_fallback(
    reason: str,
) -> None:
    primary = _StaticSearch(
        VectorSearchResult(
            (),
            {"status": "degraded"},
            "qdrant",
            "qdrant",
            False,
            reason,
        )
    )
    port = FallbackVectorSearch(
        primary=primary,
        fallback=_ForbiddenSearch(),
        policy=AvailabilityPolicy(
            "explicit_json_fallback",
            "json",
        ),
        fallback_compatibility=lambda _query: True,
    )

    with pytest.raises(
        VectorStoreFailClosedError,
        match=f"^{reason}$",
    ):
        port.search_by_vector(
            VectorSearchQuery((1.0,), 1, _scope())
        )


def test_explicit_json_fallback_requires_compatible_state_and_is_trace_visible() -> None:
    primary = _StaticSearch(
        VectorSearchResult(
            (),
            {"status": "degraded"},
            "qdrant",
            "qdrant",
            False,
            "qdrant_timeout",
        )
    )
    fallback = _StaticSearch(
        VectorSearchResult(
            (VectorSearchHit("json", 1.0, {}),),
            {"status": "ready"},
            "json",
            "json",
            False,
            "ok",
        )
    )
    port = FallbackVectorSearch(
        primary=primary,
        fallback=fallback,
        policy=AvailabilityPolicy("explicit_json_fallback", "json"),
        fallback_compatibility=lambda query: True,
    )

    result = port.search_by_vector(VectorSearchQuery((1.0,), 1, _scope()))

    assert result.provider_fallback is True
    assert result.requested_provider == "qdrant"
    assert result.effective_provider == "json"
    assert result.diagnostics["provider_fallback"] is True


def test_incompatible_json_fallback_fails_closed() -> None:
    primary = _StaticSearch(
        VectorSearchResult((), {}, "qdrant", "qdrant", False, "qdrant_unavailable")
    )
    port = FallbackVectorSearch(
        primary=primary,
        fallback=primary,
        policy=AvailabilityPolicy("explicit_json_fallback", "json"),
        fallback_compatibility=lambda query: False,
    )
    result = port.search_by_vector(VectorSearchQuery((1.0,), 1, _scope()))
    assert result.hits == ()
    assert result.reason == "fallback_state_incompatible"


def test_json_fallback_rejects_non_cosine_state_instead_of_misranking(
    tmp_path,
) -> None:
    compatibility = replace(_compatibility(), distance="dot")
    fallback = JsonVectorStore(index_path=tmp_path / "dot-vectors.json")
    fallback.rebuild(
        [_point("one", (1.0, 0.0, 0.0))],
        compatibility=compatibility,
    )
    primary = _StaticSearch(
        VectorSearchResult(
            (),
            {"status": "degraded"},
            "qdrant",
            "qdrant",
            False,
            "qdrant_unavailable",
        )
    )
    port = FallbackVectorSearch(
        primary=primary,
        fallback=fallback,
        policy=AvailabilityPolicy("explicit_json_fallback", "json"),
        fallback_compatibility=lambda query: (
            fallback.compatibility_reason(query.compatibility) == "unchanged"
        ),
    )

    result = port.search_by_vector(
        VectorSearchQuery(
            (1.0, 0.0, 0.0),
            1,
            _scope(),
            compatibility=compatibility,
        )
    )

    assert fallback.compatibility_reason(compatibility) == (
        "fallback_state_incompatible"
    )
    assert result.reason == "fallback_state_incompatible"
    assert result.hits == ()
