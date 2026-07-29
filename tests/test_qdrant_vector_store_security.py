from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from tests.qdrant_test_support import FakeQdrantClient
from worker.retrieval.qdrant_client_port import (
    QdrantClientError,
    normalise_origin,
    validate_endpoint_policy,
)
from worker.retrieval.qdrant_collection_manager import QdrantCollectionManager
from worker.retrieval.qdrant_collection_schema import to_client_point
from worker.retrieval.qdrant_filter_builder import QdrantFilterBuilder
from worker.retrieval.qdrant_vector_store import QdrantVectorStore
from worker.retrieval.vector_store_contract import (
    CompatibilitySpec,
    PreparedVectorPoint,
    VectorScope,
    VectorSearchQuery,
    VectorStoreFilters,
)


def _compatibility() -> CompatibilitySpec:
    return CompatibilitySpec(
        2, "cosine", "p", "m", "default", "float32", "cfg", "vector_store.v1", "one"
    )


def _point(scope: VectorScope) -> PreparedVectorPoint:
    return PreparedVectorPoint("same", (1.0, 0.0), scope, {"kind": "code"}, "hash")


def test_missing_scope_fails_closed_without_query() -> None:
    client = FakeQdrantClient()
    store = QdrantVectorStore(
        client=client,
        collection_manager=QdrantCollectionManager(client),
    )
    result = store.search_by_vector(VectorSearchQuery((1.0, 0.0), 10, None))
    assert result.reason == "vector_scope_required"
    assert client.calls["query_points"] == 0


def test_identical_vectors_are_isolated_by_server_side_scope() -> None:
    client = FakeQdrantClient()
    store = QdrantVectorStore(
        client=client,
        collection_manager=QdrantCollectionManager(client),
    )
    scope_a = VectorScope("a", "repo", "default", "codecompass")
    scope_b = VectorScope("b", "repo", "default", "codecompass")
    store.rebuild([_point(scope_a)], compatibility=_compatibility())
    store.rebuild([_point(scope_b)], compatibility=_compatibility())

    result = store.search_by_vector(VectorSearchQuery((1.0, 0.0), 10, scope_a))

    assert len(result.hits) == 1
    assert result.hits[0].payload["workspace_id"] == "a"


def test_filter_cannot_override_trusted_profile() -> None:
    builder = QdrantFilterBuilder()
    scope = VectorScope("a", "repo", "trusted", "codecompass")
    with pytest.raises(Exception) as exc:
        builder.build(scope=scope, filters=VectorStoreFilters(profile_name="other"))
    assert "vector_scope_conflict" in str(exc.value)


def test_role_label_filter_requires_every_requested_label() -> None:
    scope = VectorScope("a", "repo", "trusted", "codecompass")
    server_filter = QdrantFilterBuilder().build(
        scope=scope,
        filters=VectorStoreFilters(role_labels=("reader", "admin")),
    )

    role_conditions = [
        condition
        for condition in server_filter.must
        if condition.key == "role_labels"
    ]

    assert [condition.values for condition in role_conditions] == [
        ("reader",),
        ("admin",),
    ]
    assert all(condition.match == "value" for condition in role_conditions)


def test_payload_cannot_override_trusted_point_scope() -> None:
    scope = VectorScope("trusted", "repo", "default", "codecompass")
    client = FakeQdrantClient()
    store = QdrantVectorStore(
        client=client,
        collection_manager=QdrantCollectionManager(client),
    )
    point = PreparedVectorPoint(
        "record",
        (1.0, 0.0),
        scope,
        {
            "workspace_id": "attacker",
            "repository_id": "other",
            "profile_name": "forged",
            "domain": "wiki",
        },
        "hash",
    )

    store.rebuild([point], compatibility=_compatibility())
    active = store.collection_manager.active_collection(scope)
    stored = next(
        item
        for item in client.collections[active]["points"].values()
        if item.payload.get("_ananta_record_type") == "record"
    )

    assert stored.payload["workspace_id"] == "trusted"
    assert stored.payload["repository_id"] == "repo"
    assert stored.payload["profile_name"] == "default"
    assert stored.payload["domain"] == "codecompass"


def test_nested_metadata_cannot_smuggle_secrets_vectors_or_document_text() -> None:
    scope = VectorScope("trusted", "repo", "default", "codecompass")
    point = PreparedVectorPoint(
        "record",
        (1.0, 0.0),
        scope,
        {
            "kind": "code",
            "embedding_text": "explicitly opted-in excerpt",
            "metadata": {
                "language": "python",
                "API-Key": "api-secret",
                "nested": {
                    "authorization": "bearer-secret",
                    "embeddingText": "hidden document",
                    "raw-content": "hidden source",
                    "client_secret": "client-secret",
                    "safe": "kept",
                },
                "items": [
                    {
                        "vector": [1.0, 0.0],
                        "token": "token-secret",
                        "owner": "team",
                    }
                ],
            },
        },
        "hash",
    )

    stored = to_client_point(
        point,
        _compatibility(),
        store_embedding_text=True,
    )
    serialized_metadata = json.dumps(stored.payload["metadata"], sort_keys=True)

    assert stored.payload["embedding_text"] == "explicitly opted-in excerpt"
    assert stored.payload["metadata"] == {
        "language": "python",
        "nested": {"safe": "kept"},
        "items": [{"owner": "team"}],
    }
    for forbidden in (
        "api-secret",
        "bearer-secret",
        "hidden document",
        "hidden source",
        "client-secret",
        "token-secret",
        "1.0",
    ):
        assert forbidden not in serialized_metadata


def test_point_and_scope_delete_reject_cross_scope_payloads() -> None:
    scope_a = VectorScope("a", "repo", "default", "codecompass")
    scope_b = VectorScope("b", "repo", "default", "codecompass")
    client = FakeQdrantClient()
    store = QdrantVectorStore(
        client=client,
        collection_manager=QdrantCollectionManager(client),
    )
    store.rebuild([_point(scope_a)], compatibility=_compatibility())
    active = store.collection_manager.active_collection(scope_a)
    foreign = to_client_point(_point(scope_b), _compatibility())
    client.upsert(active, [foreign])

    rejected = store.delete([foreign.point_id], scope=scope_a)
    cleaned = store.delete_scope(scope_a)

    assert rejected.reason == "vector_scope_conflict"
    assert rejected.deleted == 0
    assert foreign.point_id in client.collections[active]["points"]
    assert cleaned.deleted == 1
    assert foreign.point_id in client.collections[active]["points"]


@pytest.mark.parametrize(
    "origin",
    [
        "http://user:secret@localhost:6333",
        "http://localhost:6333/path",
        "http://localhost:6333?api_key=secret",
        "http://localhost:6333#secret",
    ],
)
def test_origin_rejects_userinfo_path_query_and_fragment(origin: str) -> None:
    with pytest.raises(QdrantClientError) as exc:
        normalise_origin(origin)
    assert exc.value.reason == "vector_store_invalid_origin"
    assert "secret" not in str(exc.value)


@dataclass
class _Endpoint:
    rest_url: str
    grpc_url: str | None
    allowed_origins: tuple[str, ...]
    external_calls_allowed: bool
    tls_verify: bool = True


def test_local_origin_still_requires_allowlist() -> None:
    endpoint = _Endpoint("http://localhost:6333", None, (), False)
    with pytest.raises(QdrantClientError) as exc:
        validate_endpoint_policy(endpoint)
    assert exc.value.reason == "vector_store_invalid_origin"


def test_remote_origin_requires_opt_in_and_tls() -> None:
    endpoint = _Endpoint(
        "https://qdrant.example:6333",
        None,
        ("https://qdrant.example:6333",),
        False,
    )
    with pytest.raises(QdrantClientError):
        validate_endpoint_policy(endpoint)
    insecure = _Endpoint(
        "http://qdrant.example:6333",
        None,
        ("http://qdrant.example:6333",),
        True,
    )
    with pytest.raises(QdrantClientError) as exc:
        validate_endpoint_policy(insecure)
    assert exc.value.reason == "vector_store_tls_policy_violation"
