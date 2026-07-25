from __future__ import annotations

from dataclasses import dataclass, replace

from tests.qdrant_test_support import FakeQdrantClient
from worker.retrieval.qdrant_collection_manager import QdrantCollectionManager
from worker.retrieval.qdrant_vector_store import QdrantVectorStore
from worker.retrieval.vector_store_contract import (
    CompatibilitySpec,
    PreparedVectorPoint,
    VectorScope,
    VectorSearchHit,
    VectorSearchQuery,
    VectorSearchResult,
    VectorStoreFilters,
)
from worker.retrieval.vector_store_fallback import FallbackVectorSearch
from worker.retrieval.vector_store_config import AvailabilityPolicy


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


def _store():
    client = FakeQdrantClient()
    manager = QdrantCollectionManager(client)
    return client, QdrantVectorStore(client=client, collection_manager=manager)


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


def test_upsert_skips_unchanged_source_hash_and_is_idempotent() -> None:
    _, store = _store()
    point = _point("one", (1.0, 0.0, 0.0))
    store.rebuild([point], compatibility=_compatibility())

    result = store.upsert([point])

    assert result.upserted == 0
    assert result.skipped == 1
    assert result.failed == 0


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


def test_delete_is_scope_checked_and_rename_is_upsert_then_delete() -> None:
    _, store = _store()
    old = _point("old", (1.0, 0.0, 0.0))
    store.rebuild([old], compatibility=_compatibility())
    renamed = _point("new", (1.0, 0.0, 0.0))

    result = store.rename("old", renamed)
    search = store.search_by_vector(VectorSearchQuery((1.0, 0.0, 0.0), 10, _scope()))

    assert result.status == "ok"
    assert [hit.record_id for hit in search.hits] == ["new"]


@dataclass
class _StaticSearch:
    result: VectorSearchResult

    def search_by_vector(self, query):
        return self.result


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
