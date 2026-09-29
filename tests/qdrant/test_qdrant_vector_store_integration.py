from __future__ import annotations

import hashlib
import json
import os
import ssl
from importlib.metadata import version
from pathlib import Path
from uuid import uuid4

import pytest

from worker.retrieval.embedding_provider import (
    FakeEmbeddingProvider,
    HashEmbeddingProvider,
)
from worker.retrieval.json_vector_store import JsonVectorStore
from worker.retrieval.qdrant_client_port import (
    ClientAvailability,
    QdrantClientAdapter,
    QdrantClientError,
)
from worker.retrieval.qdrant_vector_store import QdrantVectorStore
from worker.retrieval.vector_encoding import VectorEncodingProfile
from worker.retrieval.vector_index_artifact_locator import (
    VectorIndexArtifactLocator,
)
from worker.retrieval.vector_index_execution import (
    ConfiguredVectorIndexExecution,
)
from worker.retrieval.vector_index_input_loader import (
    BoundedVectorIndexInputLoader,
)
from worker.retrieval.vector_store_config import (
    AvailabilityPolicy,
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
from worker.retrieval.vector_store_fallback import (
    ClientAvailabilityProbe,
    FallbackVectorSearch,
)
from worker.retrieval.vector_store_migration import JsonToQdrantMigrator
from worker.retrieval.wiki_vector_store import WikiVectorStore, WikiVectorStoreConfig

qdrant_client = pytest.importorskip("qdrant_client")

pytestmark = [pytest.mark.integration, pytest.mark.qdrant_integration]


def test_pinned_qdrant_client_receives_split_transport_timeouts() -> None:
    assert version("qdrant-client") == "1.18.0"
    adapter = QdrantClientAdapter(
        rest_origin="http://127.0.0.1:6333",
        connect_timeout_seconds=1.25,
        timeout_seconds=7.5,
        prefer_grpc=True,
    )
    try:
        remote = adapter._client._client
        rest_timeout = remote.openapi_client.client._client.timeout

        assert rest_timeout.connect == 1.25
        assert rest_timeout.read == 7.5
        assert rest_timeout.write == 7.5
        assert rest_timeout.pool == 7.5
        assert remote._timeout == 7.5
        assert remote._grpc_options[
            "grpc.max_reconnect_backoff_ms"
        ] == 1250
        assert remote._ananta_grpc_connect_timeout_seconds == 1.25
    finally:
        adapter.close()


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


def _real_components(
    prefix: str,
) -> tuple[object, QdrantVectorStoreConfig, EnvFileSecretResolver]:
    api_key = str(os.environ.get("ANANTA_QDRANT_API_KEY") or "").strip()
    assert api_key, "ANANTA_QDRANT_API_KEY is required for the integration profile"
    rest_url = str(os.environ.get("ANANTA_QDRANT_URL") or "").strip()
    assert rest_url.startswith("https://"), "real Qdrant integration requires HTTPS"
    ca_path = Path(
        str(os.environ.get("ANANTA_QDRANT_TLS_CA_FILE") or "").strip()
    ).resolve(strict=True)
    resolver = EnvFileSecretResolver(
        environ={"ANANTA_QDRANT_API_KEY": api_key},
        allowed_file_roots=(ca_path.parent,),
    )
    config = QdrantVectorStoreConfig(
        endpoint=QdrantEndpointConfig(
            rest_url=rest_url,
            api_key_ref="env://ANANTA_QDRANT_API_KEY",
            tls_ca_cert_ref=f"secretfile://{ca_path}",
            allowed_origins=(rest_url,),
            external_calls_allowed=False,
        ),
        collection_prefix=prefix,
    )
    raw_client = qdrant_client.QdrantClient(
        url=rest_url,
        api_key=api_key,
        verify=ssl.create_default_context(cafile=str(ca_path)),
        check_compatibility=False,
    )
    return raw_client, config, resolver


def _real_store(prefix: str) -> tuple[object, QdrantVectorStore]:
    raw_client, config, resolver = _real_components(prefix)
    store = QdrantVectorStore.from_config(
        config,
        secret_resolver=resolver,
    )
    return raw_client, store


def test_real_qdrant_crud_filters_source_hash_and_atomic_alias_swap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prefix = f"qit-{uuid4().hex[:12]}"
    raw_client, store = _real_store(prefix)
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
        unchanged = store.upsert((points[0],))
        assert unchanged.status == "ok"
        assert unchanged.upserted == 0
        assert unchanged.skipped == 1

        unfiltered = store.search_by_vector(
            VectorSearchQuery(
                (1.0, 0.0, 0.0),
                top_k=3,
                scope=scope,
                compatibility=compatibility_v1,
            )
        )
        assert [hit.record_id for hit in unfiltered.hits][:2] == ["doc-a", "doc-b"]

        filtered = store.search_by_vector(
            VectorSearchQuery(
                (1.0, 0.0, 0.0),
                top_k=3,
                scope=scope,
                filters=VectorStoreFilters(kinds=("code",), file_prefix="src"),
                compatibility=compatibility_v1,
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
            VectorSearchQuery(
                (0.95, 0.05, 0.0),
                top_k=4,
                scope=scope,
                compatibility=compatibility_v1,
            )
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
            VectorSearchQuery(
                (1.0, 0.0, 0.0),
                top_k=10,
                scope=scope,
                compatibility=compatibility_v2,
            )
        )
        assert {hit.record_id for hit in final.hits} == {"doc-a", "doc-b"}

        collections_before_failure = set(
            store.collection_manager.client.list_collections(prefix=prefix)
        )
        active_before_failure = store.collection_manager.active_collection(scope)

        def _reject_alias_swap(_alias_name: str, _collection_name: str) -> None:
            raise QdrantClientError(
                "qdrant_unavailable",
                operation="swap_alias",
                retryable=True,
            )

        monkeypatch.setattr(
            store.collection_manager.client,
            "swap_alias",
            _reject_alias_swap,
        )
        failed = store.rebuild(
            points,
            compatibility=CompatibilitySpec(
                dimensions=3,
                provider="integration",
                model="fixed",
                profile="benchmark",
                config_hash="config-v1",
                manifest_hash="manifest-failed-rebuild",
            ),
        )
        assert failed.status == "failed"
        assert failed.reason == "qdrant_unavailable"
        assert (
            store.collection_manager.active_collection(scope)
            == active_before_failure
        )
        assert set(
            store.collection_manager.client.list_collections(prefix=prefix)
        ) == collections_before_failure
        after_failed_rebuild = store.search_by_vector(
            VectorSearchQuery(
                (1.0, 0.0, 0.0),
                top_k=10,
                scope=scope,
                compatibility=compatibility_v2,
            )
        )
        assert {hit.record_id for hit in after_failed_rebuild.hits} == {
            "doc-a",
            "doc-b",
        }
    finally:
        store.close()
        try:
            _cleanup(raw_client, prefix)
        finally:
            raw_client.close()


def test_real_worker_prepares_digest_bound_documents_and_rebuilds(
    tmp_path: Path,
) -> None:
    prefix = f"qtask-{uuid4().hex[:10]}"
    raw_client, qdrant_config, secret_resolver = _real_components(
        prefix
    )
    verification_store = QdrantVectorStore.from_config(
        qdrant_config,
        secret_resolver=secret_resolver,
    )
    scope = VectorScope(
        "workspace-task-it",
        "repository-task-it",
        "default",
        "codecompass",
    )
    compatibility = CompatibilitySpec(
        dimensions=12,
        provider="local_hash",
        model="hash-v1",
        profile="codecompass-symbol-path-summary-v1",
        encoding="float32",
        config_hash="task-config-v1",
        schema_version="codecompass_vector_index.v2",
        manifest_hash="task-manifest-v1",
    )
    input_root = tmp_path / "vector-inputs"
    input_root.mkdir()
    raw = json.dumps(
        {
            "schema": "ananta.vector_index_documents.v1",
            "kind": "codecompass_documents",
            "documents": [
                {
                    "record_id": "task-doc-a",
                    "kind": "python_function",
                    "file": "src/task.py",
                    "source_scope": "repo",
                    "profile_name": "default",
                    "manifest_hash": "task-manifest-v1",
                    "embedding_text": "delegated payment retry",
                    "source_hash": "task-doc-a-v1",
                }
            ],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    input_ref = VectorIndexArtifactLocator.locate(
        scope=scope,
        content_sha256=hashlib.sha256(raw).hexdigest(),
    ).to_reference()
    source = input_root / input_ref["path"]
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(raw)
    execution = ConfiguredVectorIndexExecution(
        secret_resolver=secret_resolver,
        input_loader=BoundedVectorIndexInputLoader(
            allowed_roots=(input_root,)
        ),
    )
    resolved_config = {
        "schema": "ananta.vector_store_resolved_config.v1",
        "config": {
            "provider": "qdrant",
            "qdrant": qdrant_config.as_dict(),
        },
    }
    try:
        result = execution.execute(
            operation="rebuild",
            scope=scope.as_dict(),
            resolved_config=resolved_config,
            payload={
                "input_ref": input_ref,
                "preparation": {
                    "schema": "ananta.vector_index_preparation.v1",
                    "kind": "codecompass_documents",
                    "embedding": {
                        "provider": "local_hash",
                        "provider_id": "local_hash",
                        "model_version": "hash-v1",
                        "dimensions": 12,
                        "timeout_seconds": 20,
                        "external_calls_allowed": False,
                        "allowed_base_urls": [],
                    },
                    "embedding_text_profile": (
                        "codecompass-symbol-path-summary-v1"
                    ),
                },
                "compatibility": compatibility.as_dict(),
                "batch_size": 1,
            },
            idempotency_key="real-document-task-it",
        )

        assert result["status"] == "completed"
        assert result["result"]["indexed_documents"] == 1
        provider = HashEmbeddingProvider(dimensions=12)
        hits = verification_store.search_by_vector(
            VectorSearchQuery(
                tuple(
                    provider.embed_texts(
                        ["delegated payment retry"]
                    )[0]
                ),
                top_k=2,
                scope=scope,
                compatibility=compatibility,
            )
        )
        assert [hit.record_id for hit in hits.hits] == ["task-doc-a"]
    finally:
        verification_store.close()
        try:
            _cleanup(raw_client, prefix)
        finally:
            raw_client.close()


def test_real_json_to_qdrant_migration_pause_resume_and_preservation(
    tmp_path,
) -> None:
    prefix = f"qmi-{uuid4().hex[:12]}"
    raw_client, store = _real_store(prefix)
    scope = VectorScope(
        "workspace-migration-it",
        "repository-migration-it",
        "default",
        "codecompass",
    )
    compatibility = CompatibilitySpec(
        dimensions=3,
        provider="integration",
        model="fixed",
        profile="migration",
        config_hash="migration-config-v1",
        manifest_hash="migration-manifest-v1",
    )
    source = tmp_path / "codecompass-vector-index.json"
    source.write_text(
        json.dumps(
            {
                "state": {
                    "schema": "codecompass_vector_index.v2",
                    "distance": compatibility.distance,
                    "embedding_provider": compatibility.provider,
                    "embedding_model_name": compatibility.model,
                    "embedding_dimensions": compatibility.dimensions,
                    "embedding_provider_config_hash": compatibility.config_hash,
                    "embedding_text_profile": compatibility.profile,
                    "manifest_hash": compatibility.manifest_hash,
                    "vector_encoding_profile": {"mode": "off"},
                },
                "entries": [
                    {
                        "record_id": "migration-a",
                        "source_hash": "migration-a-v1",
                        "vector": [1.0, 0.0, 0.0],
                        "kind": "code",
                        "source_scope": "repository",
                    },
                    {
                        "record_id": "migration-b",
                        "source_hash": "migration-b-v1",
                        "vector": [0.0, 1.0, 0.0],
                        "kind": "documentation",
                        "source_scope": "repository",
                    },
                ],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    original = source.read_bytes()
    migrator = JsonToQdrantMigrator(store)
    try:
        plan = migrator.dry_run(
            source,
            scope=scope,
            compatibility=compatibility,
        )
        assert plan.status == "ready"
        assert plan.source_entries == 2
        assert store.collection_manager.active_collection(scope) is None

        paused = migrator.migrate(
            source,
            scope=scope,
            compatibility=compatibility,
            batch_size=1,
            max_batches=1,
            idempotency_key="real-migration-it",
        )
        assert paused.result.reason == "migration_paused"
        assert paused.checkpoint is not None
        assert store.collection_manager.active_collection(scope) is None

        completed = migrator.migrate(
            source,
            scope=scope,
            compatibility=compatibility,
            checkpoint=paused.checkpoint,
            batch_size=1,
            idempotency_key="real-migration-it",
        )
        assert completed.result.status == "ok"
        assert completed.activated is True
        assert source.read_bytes() == original

        migrated = store.search_by_vector(
            VectorSearchQuery(
                (1.0, 0.0, 0.0),
                top_k=10,
                scope=scope,
                compatibility=compatibility,
            )
        )
        assert {hit.record_id for hit in migrated.hits} == {
            "migration-a",
            "migration-b",
        }
    finally:
        store.close()
        try:
            _cleanup(raw_client, prefix)
        finally:
            raw_client.close()


def test_real_qdrant_outage_uses_only_compatible_json_fallback(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prefix = f"qfi-{uuid4().hex[:12]}"
    raw_client, store = _real_store(prefix)
    scope = VectorScope(
        "workspace-fallback-it",
        "repository-fallback-it",
        "default",
        "codecompass",
    )
    compatibility = CompatibilitySpec(
        dimensions=3,
        provider="integration",
        model="fixed",
        profile="fallback",
        encoding=VectorEncodingProfile.disabled().config_hash(),
        config_hash="fallback-config-v1",
        manifest_hash="fallback-manifest-v1",
    )
    points = (
        PreparedVectorPoint(
            "fallback-a",
            (1.0, 0.0, 0.0),
            scope,
            {
                "kind": "code",
                "source_scope": "repository",
                "role_labels": ["reader"],
            },
            source_hash="fallback-a-v1",
        ),
    )
    json_store = JsonVectorStore(index_path=tmp_path / "fallback-index.json")
    try:
        assert store.rebuild(points, compatibility=compatibility).status == "ok"
        assert (
            json_store.rebuild(points, compatibility=compatibility).status
            == "ok"
        )
        assert json_store.compatibility_reason(compatibility) == "unchanged"
        monkeypatch.setattr(
            store.collection_manager.client,
            "probe",
            lambda: ClientAvailability(
                status="unavailable",
                reason="qdrant_unavailable",
            ),
        )
        fallback = FallbackVectorSearch(
            primary=store,
            fallback=json_store,
            policy=AvailabilityPolicy(
                on_unavailable="explicit_json_fallback",
                fallback_provider="json",
            ),
            availability_probe=ClientAvailabilityProbe(
                store.collection_manager.client
            ),
            fallback_compatibility=lambda query: (
                query.compatibility is not None
                and json_store.compatibility_reason(query.compatibility)
                == "unchanged"
            ),
        )

        result = fallback.search_by_vector(
            VectorSearchQuery(
                (1.0, 0.0, 0.0),
                top_k=5,
                scope=scope,
                compatibility=compatibility,
            )
        )

        assert [hit.record_id for hit in result.hits] == ["fallback-a"]
        assert result.requested_provider == "qdrant"
        assert result.effective_provider == "json"
        assert result.provider_fallback is True
        assert result.reason == "qdrant_unavailable"
    finally:
        json_store.close()
        store.close()
        try:
            _cleanup(raw_client, prefix)
        finally:
            raw_client.close()


def test_real_wiki_qdrant_index_search_delete_and_scope_isolation() -> None:
    api_key = str(os.environ.get("ANANTA_QDRANT_API_KEY") or "").strip()
    assert api_key, "ANANTA_QDRANT_API_KEY is required for the integration profile"
    rest_url = str(os.environ.get("ANANTA_QDRANT_URL") or "").strip()
    assert rest_url.startswith("https://"), "real Qdrant integration requires HTTPS"
    ca_path = Path(
        str(os.environ.get("ANANTA_QDRANT_TLS_CA_FILE") or "").strip()
    ).resolve(strict=True)
    prefix = f"ananta-wiki-{uuid4().hex[:8]}"
    resolver = EnvFileSecretResolver(
        environ={"ANANTA_QDRANT_API_KEY": api_key},
        allowed_file_roots=(ca_path.parent,),
    )
    qdrant = QdrantVectorStoreConfig(
        endpoint=QdrantEndpointConfig(
            rest_url=rest_url,
            api_key_ref="env://ANANTA_QDRANT_API_KEY",
            tls_ca_cert_ref=f"secretfile://{ca_path}",
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
    raw_client = qdrant_client.QdrantClient(
        url=rest_url,
        api_key=api_key,
        verify=ssl.create_default_context(cafile=str(ca_path)),
        check_compatibility=False,
    )
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
