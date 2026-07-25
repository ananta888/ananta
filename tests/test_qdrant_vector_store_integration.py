from __future__ import annotations

import os
from uuid import uuid4

import pytest

qdrant_client = pytest.importorskip("qdrant_client")

from worker.retrieval.qdrant_vector_store import QdrantVectorStore
from worker.retrieval.vector_store_config import (
    QdrantEndpointConfig,
    QdrantVectorStoreConfig,
)
from worker.retrieval.vector_store_contract import (
    CompatibilitySpec,
    PreparedVectorPoint,
    VectorScope,
    VectorSearchQuery,
    VectorStoreFilters,
)
from worker.retrieval.vector_store_endpoint_policy import EnvFileSecretResolver
from worker.retrieval.embedding_provider import FakeEmbeddingProvider
from worker.retrieval.wiki_vector_store import WikiVectorStore, WikiVectorStoreConfig


pytestmark = pytest.mark.integration


def _cleanup(client: object, prefix: str) -> None:
    from qdrant_client import models

    for alias in getattr(client.get_aliases(), "aliases", ()):
        alias_name = str(getattr(alias, "alias_name", ""))
        if alias_name.startswith(prefix):
            client.update_collection_aliases(
                change_aliases_operations=[
                    models.DeleteAliasOperation(
                        delete_alias=models.DeleteAlias(alias_name=alias_name)
                    )
                ]
            )
    for item in client.get_collections().collections:
        if str(item.name).startswith(prefix):
            client.delete_collection(collection_name=item.name)


def test_real_qdrant_crud_filters_and_atomic_alias_swap() -> None:
    api_key = str(os.environ.get("ANANTA_QDRANT_API_KEY") or "").strip()
    assert api_key, "ANANTA_QDRANT_API_KEY is required for the integration profile"
    rest_url = str(os.environ.get("ANANTA_QDRANT_URL") or "http://127.0.0.1:6333")
    prefix = f"ananta_it_{uuid4().hex[:12]}"
    resolver = EnvFileSecretResolver(environ={"ANANTA_QDRANT_API_KEY": api_key})
    config = QdrantVectorStoreConfig(
        endpoint=QdrantEndpointConfig(
            rest_url=rest_url,
            api_key_ref="env://ANANTA_QDRANT_API_KEY",
            allowed_origins=(rest_url,),
            external_calls_allowed=False,
        ),
        collection_prefix=prefix,
    )
    raw_client = qdrant_client.QdrantClient(url=rest_url, api_key=api_key)
    store = QdrantVectorStore.from_config(config, secret_resolver=resolver)
    scope = VectorScope("workspace-it", "repository-it", "default", "codecompass")
    compatibility_v1 = CompatibilitySpec(
        dimensions=3,
        provider="integration",
        model="fixed",
        profile="benchmark",
        config_hash="config-v1",
        manifest_hash="manifest-v1",
    )
    points = (
        PreparedVectorPoint(
            "doc-a",
            (1.0, 0.0, 0.0),
            scope,
            {
                "kind": "code",
                "file": "src/a.py",
                "source_scope": "repository",
                "role_labels": ["reader"],
            },
            source_hash="doc-a-v1",
        ),
        PreparedVectorPoint(
            "doc-b",
            (0.8, 0.2, 0.0),
            scope,
            {
                "kind": "documentation",
                "file": "docs/b.md",
                "source_scope": "repository",
                "role_labels": ["reader"],
            },
            source_hash="doc-b-v1",
        ),
        PreparedVectorPoint(
            "doc-c",
            (0.0, 1.0, 0.0),
            scope,
            {
                "kind": "code",
                "file": "src/c.py",
                "source_scope": "repository",
                "role_labels": ["admin"],
            },
            source_hash="doc-c-v1",
        ),
    )
    try:
        rebuilt = store.rebuild(points, compatibility=compatibility_v1)
        assert rebuilt.status == "ok"
        first_collection = store.collection_manager.active_collection(scope)
        assert first_collection

        unfiltered = store.search_by_vector(
            VectorSearchQuery((1.0, 0.0, 0.0), top_k=3, scope=scope)
        )
        assert [hit.record_id for hit in unfiltered.hits][:2] == ["doc-a", "doc-b"]

        filtered = store.search_by_vector(
            VectorSearchQuery(
                (1.0, 0.0, 0.0),
                top_k=3,
                scope=scope,
                filters=VectorStoreFilters(kinds=("code",), file_prefix="src"),
            )
        )
        assert {hit.record_id for hit in filtered.hits} == {"doc-a", "doc-c"}

        updated = PreparedVectorPoint(
            "doc-b",
            (1.0, 0.0, 0.0),
            scope,
            {
                "kind": "documentation",
                "file": "docs/b.md",
                "source_scope": "repository",
                "role_labels": ["reader"],
                "revision": 2,
            },
            source_hash="doc-b-v2",
        )
        inserted = PreparedVectorPoint(
            "doc-d",
            (0.95, 0.05, 0.0),
            scope,
            {
                "kind": "code",
                "file": "src/d.py",
                "source_scope": "repository",
                "role_labels": ["reader"],
            },
            source_hash="doc-d-v1",
        )
        upserted = store.upsert((updated, inserted))
        assert upserted.status == "ok"
        assert upserted.upserted == 2
        deleted = store.delete(("doc-d",), scope=scope)
        assert deleted.status == "ok"
        assert deleted.deleted == 1
        after_delete = store.search_by_vector(
            VectorSearchQuery((0.95, 0.05, 0.0), top_k=4, scope=scope)
        )
        assert "doc-d" not in {hit.record_id for hit in after_delete.hits}

        compatibility_v2 = CompatibilitySpec(
            dimensions=3,
            provider="integration",
            model="fixed",
            profile="benchmark",
            config_hash="config-v1",
            manifest_hash="manifest-v2",
        )
        swapped = store.rebuild(points[:2], compatibility=compatibility_v2)
        assert swapped.status == "ok"
        second_collection = store.collection_manager.active_collection(scope)
        assert second_collection and second_collection != first_collection
        assert swapped.diagnostics["alias_changed"] is True
        final = store.search_by_vector(
            VectorSearchQuery((1.0, 0.0, 0.0), top_k=10, scope=scope)
        )
        assert {hit.record_id for hit in final.hits} == {"doc-a", "doc-b"}
    finally:
        store.close()
        try:
            _cleanup(raw_client, prefix)
        finally:
            raw_client.close()


def test_real_wiki_qdrant_index_search_delete_and_scope_isolation() -> None:
    api_key = str(os.environ.get("ANANTA_QDRANT_API_KEY") or "").strip()
    assert api_key, "ANANTA_QDRANT_API_KEY is required for the integration profile"
    rest_url = str(os.environ.get("ANANTA_QDRANT_URL") or "http://127.0.0.1:6333")
    prefix = f"ananta-wiki-it-{uuid4().hex[:12]}"
    resolver = EnvFileSecretResolver(environ={"ANANTA_QDRANT_API_KEY": api_key})
    qdrant = QdrantVectorStoreConfig(
        endpoint=QdrantEndpointConfig(
            rest_url=rest_url,
            api_key_ref="env://ANANTA_QDRANT_API_KEY",
            allowed_origins=(rest_url,),
            external_calls_allowed=False,
        ),
        collection_prefix=prefix,
    )
    config = WikiVectorStoreConfig(
        provider="qdrant",
        qdrant_enabled=True,
        collection_prefix=prefix,
        workspace_id="wiki-workspace-a",
        source_id="wiki-source",
        profile_name="semantic",
        qdrant=qdrant,
    )
    other_config = WikiVectorStoreConfig(
        provider="qdrant",
        qdrant_enabled=True,
        collection_prefix=prefix,
        workspace_id="wiki-workspace-b",
        source_id="wiki-source",
        profile_name="semantic",
        qdrant=qdrant,
    )
    provider = FakeEmbeddingProvider(
        provider_id="wiki-integration",
        model_version="wiki-integration-v1",
        dimensions=8,
    )
    raw_client = qdrant_client.QdrantClient(url=rest_url, api_key=api_key)
    store = WikiVectorStore(config=config, secret_resolver=resolver)
    other_store = WikiVectorStore(config=other_config, secret_resolver=resolver)
    documents = [
        {
            "record_id": "wiki:retry",
            "chunk_id": "wiki:retry",
            "kind": "wiki_section_chunk",
            "file": "wiki/retry.md",
            "embedding_text": "Ananta retry handling",
            "source_scope": "wiki",
            "manifest_hash": "wiki-manifest-v1",
        },
        {
            "record_id": "wiki:auth",
            "chunk_id": "wiki:auth",
            "kind": "wiki_section_chunk",
            "file": "wiki/auth.md",
            "embedding_text": "Token verification",
            "source_scope": "wiki",
            "manifest_hash": "wiki-manifest-v1",
        },
    ]
    try:
        rebuilt = store.rebuild(
            documents=documents,
            embedding_provider=provider,
            retrieval_cache_state="wiki-cache-v1",
            manifest_hash="wiki-manifest-v1",
        )
        assert rebuilt["status"] == "ok"
        hits = store.search(
            query="Ananta retry handling",
            embedding_provider=provider,
            top_k=2,
        )
        assert hits[0]["record_id"] == "wiki:retry"
        assert hits[0]["payload_schema"] == "ananta.wiki_vector_payload.v1"

        cross_scope = other_store.search(
            query="Ananta retry handling",
            embedding_provider=provider,
            top_k=2,
        )
        assert cross_scope == []

        deleted = store.delete(record_ids=("wiki:auth",))
        assert deleted["status"] == "ok"
        after_delete = store.search(
            query="Token verification",
            embedding_provider=provider,
            top_k=5,
        )
        assert "wiki:auth" not in {row["record_id"] for row in after_delete}
    finally:
        store.close()
        other_store.close()
        try:
            _cleanup(raw_client, prefix)
        finally:
            raw_client.close()
