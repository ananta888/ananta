from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.services.codecompass_vector_retrieval_service import CodeCompassVectorRetrievalService
from worker.retrieval.vector_index_artifact_locator import (
    VectorIndexArtifactLocator,
)
from worker.retrieval.vector_store_config import (
    AvailabilityPolicy,
    VectorStoreConfig,
)
from worker.retrieval.vector_store_contract import (
    CompatibilitySpec,
    IndexWriteResult,
    PreparedVectorPoint,
    VectorScope,
    VectorSearchHit,
    VectorSearchResult,
    VectorStoreError,
    VectorStoreFailClosedError,
)
from worker.retrieval.vector_store_fallback import FallbackVectorSearch


class _RecordingStore:
    def __init__(self) -> None:
        self.search_queries = []
        self.refresh_calls = []
        self.close_calls = 0

    def search_by_vector(self, query):
        self.search_queries.append(query)
        return VectorSearchResult(
            hits=(),
            diagnostics={},
            requested_provider="json",
            effective_provider="json",
            reason="empty_index",
        )

    def refresh(self, points, *, compatibility):
        self.refresh_calls.append((points, compatibility))
        return IndexWriteResult("ok", "refresh", "refresh", len(points))

    def rebuild(self, points, *, compatibility):
        return self.refresh(points, compatibility=compatibility)

    def upsert(self, points, *, batch_size=128):
        return IndexWriteResult("ok", "upsert", "upsert", len(points))

    def delete(self, point_ids, *, scope):
        return IndexWriteResult("ok", "delete", "delete", 0)

    def delete_scope(self, scope):
        return IndexWriteResult("ok", "delete", "delete", 0)

    def diagnostics(self):
        raise AssertionError("diagnostics not used")

    def close(self):
        self.close_calls += 1


class _UnavailableVectorSearch:
    def __init__(self, reason: str = "qdrant_unavailable") -> None:
        self._reason = reason

    def search_by_vector(self, query):
        del query
        raise VectorStoreError(self._reason)


class _StaticVectorSearch:
    def __init__(self, *hits: VectorSearchHit) -> None:
        self._hits = tuple(hits)

    def search_by_vector(self, query):
        del query
        return VectorSearchResult(
            hits=self._hits,
            diagnostics={"status": "ready"},
            requested_provider="json",
            effective_provider="json",
            reason="ok",
        )


def _qdrant_store_config(
    tmp_path: Path,
    mode: str,
) -> VectorStoreConfig:
    return VectorStoreConfig.from_mapping(
        {
            "provider": "qdrant",
            "availability": {"on_unavailable": mode},
            "json": {
                "index_path": str(tmp_path / "fallback.json"),
            },
            "qdrant": {},
        }
    )


def _availability_search(
    mode: str,
    *,
    fallback=None,
    primary_reason: str = "qdrant_unavailable",
) -> FallbackVectorSearch:
    return FallbackVectorSearch(
        primary=_UnavailableVectorSearch(primary_reason),
        fallback=fallback,
        policy=AvailabilityPolicy(mode),
        fallback_compatibility=lambda _query: True,
    )


def _write_codecompass_fixture(root: Path) -> None:
    out = root / "rag-helper" / "out"
    out.mkdir(parents=True)
    (out / "manifest.json").write_text(
        json.dumps(
            {
                "manifest_hash": "mh-fixture",
                "profile_name": "python",
                "source_scope": "repo",
                "retrieval_cache_state": "cache-fixture",
            }
        ),
        encoding="utf-8",
    )
    (out / "embedding.json").write_text(
        json.dumps(
            {
                "records": [
                    {
                        "id": "emb-payment",
                        "kind": "python_function",
                        "file": "src/payment.py",
                        "embedding_text": "payment retry timeout service",
                        "_provenance": {"output_kind": "embedding", "record_id": "emb-payment"},
                    },
                    {
                        "id": "emb-invoice",
                        "kind": "python_function",
                        "file": "src/invoice.py",
                        "embedding_text": "invoice tax calculation",
                        "_provenance": {"output_kind": "embedding", "record_id": "emb-invoice"},
                    },
                    {
                        "id": "emb-doc",
                        "kind": "markdown_doc",
                        "file": "docs/retrieval.md",
                        "embedding_text": "retrieval architecture notes",
                        "_provenance": {"output_kind": "embedding", "record_id": "emb-doc"},
                    },
                ]
            }
        ),
        encoding="utf-8",
    )


def test_codecompass_vector_retrieval_service_indexes_and_searches_without_network(tmp_path: Path) -> None:
    _write_codecompass_fixture(tmp_path)
    service = CodeCompassVectorRetrievalService(
        repo_root=tmp_path,
        embedding_records_path="rag-helper/out/embedding.json",
        manifest_path="rag-helper/out/manifest.json",
        index_path=".rag/codecompass/vector_index.json",
        provider_config={"provider": "local_hash", "model_version": "hash-v1", "dimensions": 12},
    )

    refresh = service.refresh_index()
    rows = service.search(query="payment timeout", top_k=2)
    diagnostic = service.last_diagnostic()

    assert refresh["status"] == "ok"
    assert rows
    assert rows[0]["engine"] == "codecompass_vector"
    assert rows[0]["metadata"]["record_id"]
    assert "vector_score" in rows[0]["metadata"]
    assert diagnostic["status"] == "ready"
    assert refresh["diagnostics"]["manifest_hash"] == "mh-fixture"
    assert diagnostic["manifest_hash"] == "mh-fixture"


def test_codecompass_vector_retrieval_close_delegates_once(
    tmp_path: Path,
) -> None:
    store = _RecordingStore()
    service = CodeCompassVectorRetrievalService(
        repo_root=tmp_path,
        embedding_records_path="embedding.json",
        manifest_path="manifest.json",
        index_path="vector.json",
        vector_store=store,
    )

    service.close()
    service.close()

    assert store.close_calls == 1


def test_codecompass_vector_retrieval_service_search_is_read_only_when_index_is_missing(tmp_path: Path) -> None:
    service = CodeCompassVectorRetrievalService(
        repo_root=tmp_path,
        embedding_records_path="rag-helper/out/embedding.json",
        manifest_path="rag-helper/out/manifest.json",
        index_path=".rag/codecompass/vector_index.json",
    )

    assert service.search(query="anything") == []
    assert service.last_diagnostic()["status"] == "degraded"
    assert service.last_diagnostic()["reason"] == "missing_index"


def test_qdrant_fail_fast_reaches_codecompass_product_caller(
    tmp_path: Path,
) -> None:
    service = CodeCompassVectorRetrievalService(
        repo_root=tmp_path,
        embedding_records_path="rag-helper/out/embedding.json",
        manifest_path="rag-helper/out/manifest.json",
        index_path=".rag/codecompass/vector_index.json",
        vector_store_config=_qdrant_store_config(
            tmp_path,
            "fail_fast",
        ),
        vector_search_port=_availability_search("fail_fast"),
    )

    with pytest.raises(
        VectorStoreError,
        match="qdrant_unavailable",
    ):
        service.search(query="anything")


@pytest.mark.parametrize(
    "mode",
    ("fail_fast", "degraded_empty", "explicit_json_fallback"),
)
@pytest.mark.parametrize(
    "reason",
    ("qdrant_unauthorized", "incompatible_collection"),
)
def test_qdrant_security_and_schema_failures_are_never_hidden_by_codecompass(
    tmp_path: Path,
    mode: str,
    reason: str,
) -> None:
    selectable_fallback = _StaticVectorSearch(
        VectorSearchHit(
            record_id="unsafe-fallback",
            score=1.0,
            payload={
                "kind": "python_function",
                "file": "src/unsafe.py",
                "embedding_text": "must not be selected",
            },
        )
    )
    service = CodeCompassVectorRetrievalService(
        repo_root=tmp_path,
        embedding_records_path="rag-helper/out/embedding.json",
        manifest_path="rag-helper/out/manifest.json",
        index_path=".rag/codecompass/vector_index.json",
        vector_store_config=_qdrant_store_config(tmp_path, mode),
        vector_search_port=_availability_search(
            mode,
            fallback=selectable_fallback,
            primary_reason=reason,
        ),
    )

    with pytest.raises(
        VectorStoreFailClosedError,
        match=f"^{reason}$",
    ):
        service.search(query="anything")


def test_qdrant_degraded_empty_remains_empty_in_codecompass_product(
    tmp_path: Path,
) -> None:
    service = CodeCompassVectorRetrievalService(
        repo_root=tmp_path,
        embedding_records_path="rag-helper/out/embedding.json",
        manifest_path="rag-helper/out/manifest.json",
        index_path=".rag/codecompass/vector_index.json",
        vector_store_config=_qdrant_store_config(
            tmp_path,
            "degraded_empty",
        ),
        vector_search_port=_availability_search("degraded_empty"),
        fail_mode="fail_fast",
    )

    assert service.search(query="anything") == []
    assert service.last_diagnostic()["status"] == "degraded"
    assert service.last_diagnostic()["reason"] == "qdrant_unavailable"


def test_explicit_json_fallback_reaches_codecompass_product(
    tmp_path: Path,
) -> None:
    fallback = _StaticVectorSearch(
        VectorSearchHit(
            record_id="fallback-record",
            score=0.92,
            payload={
                "kind": "python_function",
                "file": "src/fallback.py",
                "embedding_text": "fallback payment timeout",
            },
        )
    )
    service = CodeCompassVectorRetrievalService(
        repo_root=tmp_path,
        embedding_records_path="rag-helper/out/embedding.json",
        manifest_path="rag-helper/out/manifest.json",
        index_path=".rag/codecompass/vector_index.json",
        vector_store_config=_qdrant_store_config(
            tmp_path,
            "explicit_json_fallback",
        ),
        vector_search_port=_availability_search(
            "explicit_json_fallback",
            fallback=fallback,
        ),
    )

    rows = service.search(query="payment timeout")

    assert [row["record_id"] for row in rows] == ["fallback-record"]
    assert rows[0]["source"] == "src/fallback.py"
    assert service.last_diagnostic()["provider_fallback"] is True


def test_codecompass_vector_retrieval_service_applies_allowed_paths(tmp_path: Path) -> None:
    _write_codecompass_fixture(tmp_path)
    service = CodeCompassVectorRetrievalService(
        repo_root=tmp_path,
        embedding_records_path="rag-helper/out/embedding.json",
        manifest_path="rag-helper/out/manifest.json",
        index_path=".rag/codecompass/vector_index.json",
    )

    service.refresh_index()
    rows = service.search(query="payment timeout retrieval", top_k=5, allowed_paths=["src"])

    assert rows
    assert all(str(row["source"]).startswith("src/") for row in rows)


def test_search_passes_trusted_scope_and_expected_compatibility(tmp_path: Path) -> None:
    _write_codecompass_fixture(tmp_path)
    store = _RecordingStore()
    scope = VectorScope(
        "workspace-a",
        "repo-a",
        "semantic",
        "codecompass",
    )
    service = CodeCompassVectorRetrievalService(
        repo_root=tmp_path,
        embedding_records_path="rag-helper/out/embedding.json",
        manifest_path="rag-helper/out/manifest.json",
        index_path=".rag/codecompass/vector_index.json",
        provider_config={
            "provider": "local_hash",
            "model_version": "hash-v1",
            "dimensions": 12,
        },
        vector_store=store,
        trusted_scope=scope,
    )

    assert service.search(query="payment") == []
    query = store.search_queries[0]

    assert query.scope == scope
    assert query.compatibility is not None
    assert query.compatibility.dimensions == 12
    assert query.compatibility.model == "hash-v1"
    assert query.compatibility.manifest_hash == "mh-fixture"


def test_service_accepts_separate_minimal_search_and_write_ports(
    tmp_path: Path,
) -> None:
    _write_codecompass_fixture(tmp_path)

    class SearchPort:
        def __init__(self) -> None:
            self.queries = []

        def search_by_vector(self, query):
            self.queries.append(query)
            return VectorSearchResult(
                hits=(),
                diagnostics={},
                requested_provider="json",
                effective_provider="json",
                reason="empty_index",
            )

    class WritePort:
        def __init__(self) -> None:
            self.refresh_calls = []

        def refresh(self, points, *, compatibility):
            self.refresh_calls.append((points, compatibility))
            return IndexWriteResult(
                "ok",
                "refresh",
                "refreshed",
                len(points),
            )

    class ForbiddenFactory:
        def create(self, *_args, **_kwargs):
            raise AssertionError("narrow port injection used factory")

    search_port = SearchPort()
    write_port = WritePort()
    service = CodeCompassVectorRetrievalService(
        repo_root=tmp_path,
        embedding_records_path="rag-helper/out/embedding.json",
        manifest_path="rag-helper/out/manifest.json",
        index_path=".rag/codecompass/vector_index.json",
        provider_config={
            "provider": "local_hash",
            "model_version": "hash-v1",
            "dimensions": 12,
        },
        vector_search_port=search_port,
        vector_index_writer=write_port,
        vector_store_factory=ForbiddenFactory(),
    )

    assert service.refresh_index()["status"] == "ok"
    assert service.search(query="payment") == []
    assert len(write_port.refresh_calls) == 1
    assert len(search_port.queries) == 1


def test_refresh_delegates_write_through_hub_task_port(tmp_path: Path) -> None:
    _write_codecompass_fixture(tmp_path)
    store = _RecordingStore()
    submissions: list[dict] = []

    class TaskService:
        def submit(self, **kwargs):
            submissions.append(kwargs)
            return {
                "job_id": "vector-index-delegated",
                "status": "queued",
            }

    published: list[dict] = []

    class Publisher:
        def publish(self, **kwargs):
            published.append(kwargs)
            return VectorIndexArtifactLocator.locate(
                scope=kwargs["scope"],
                content_sha256=kwargs["content_sha256"],
            ).to_reference()

    service = CodeCompassVectorRetrievalService(
        repo_root=tmp_path,
        embedding_records_path="rag-helper/out/embedding.json",
        manifest_path="rag-helper/out/manifest.json",
        index_path=".rag/codecompass/vector_index.json",
        provider_config={
            "provider": "local_hash",
            "model_version": "hash-v1",
            "dimensions": 12,
        },
        vector_store=store,
        trusted_scope=VectorScope(
            "workspace-a",
            "repo-a",
            "default",
            "codecompass",
        ),
        index_task_service=TaskService(),
        index_input_publisher=Publisher(),
    )

    result = service.refresh_index()

    assert result["status"] == "queued"
    assert store.refresh_calls == []
    assert submissions[0]["operation"] == "refresh"
    assert submissions[0]["trusted_scope"].workspace_id == "workspace-a"
    assert "points" not in submissions[0]["payload"]
    assert submissions[0]["payload"]["preparation"]["kind"] == "codecompass_documents"
    artifact = json.loads(published[0]["content"].decode("utf-8"))
    assert artifact["schema"] == "ananta.vector_index_documents.v1"
    assert artifact["documents"][0]["embedding_text"]
    assert all("vector" not in document for document in artifact["documents"])
    assert submissions[0]["payload"]["compatibility"]["manifest_hash"] == "mh-fixture"
    assert submissions[0]["idempotency_key"].startswith("codecompass-refresh-")


def test_degraded_diagnostics_never_persist_exception_or_secret_text(
    tmp_path: Path,
) -> None:
    _write_codecompass_fixture(tmp_path)

    class FailingStore(_RecordingStore):
        def search_by_vector(self, query):
            del query
            raise RuntimeError("api_key=private-provider-secret")

        def refresh(self, points, *, compatibility):
            del points, compatibility
            raise RuntimeError("Authorization: private-provider-secret")

    service = CodeCompassVectorRetrievalService(
        repo_root=tmp_path,
        embedding_records_path="rag-helper/out/embedding.json",
        manifest_path="rag-helper/out/manifest.json",
        index_path=".rag/codecompass/vector_index.json",
        vector_store=FailingStore(),
    )

    assert service.search(query="payment") == []
    search_diagnostic = service.last_diagnostic()
    refresh = service.refresh_index()

    assert search_diagnostic == {
        "status": "degraded",
        "reason": "codecompass_vector_unavailable",
    }
    assert "private-provider-secret" not in str(search_diagnostic)
    assert "private-provider-secret" not in str(refresh)
    assert "error" not in service.last_diagnostic()


def test_large_refresh_requires_explicit_publisher_and_uses_digest_bound_ref(
    tmp_path: Path,
) -> None:
    points = [
        PreparedVectorPoint(
            record_id=f"record-{index}",
            vector=(1.0, 0.0),
            scope=VectorScope(
                "workspace-a",
                "repo-a",
                "default",
                "codecompass",
            ),
            payload={"kind": "code"},
            source_hash=f"source-{index}",
        )
        for index in range(1001)
    ]
    compatibility = CompatibilitySpec(
        dimensions=2,
        distance="cosine",
        provider="test",
        model="v1",
        profile="default",
        encoding="float32",
        config_hash="config-a",
        schema_version="vector_store.v1",
        manifest_hash="manifest-a",
    )
    submissions = []

    class TaskService:
        def submit(self, **kwargs):
            submissions.append(kwargs)
            return {"job_id": "vector-index-large", "status": "queued"}

    without_publisher = CodeCompassVectorRetrievalService(
        repo_root=tmp_path,
        embedding_records_path="unused.json",
        manifest_path="unused-manifest.json",
        index_path="unused-index.json",
        vector_store=_RecordingStore(),
        trusted_scope=points[0].scope,
        index_task_service=TaskService(),
    )
    with pytest.raises(
        VectorStoreError,
        match="vector_index_input_publisher_required",
    ):
        without_publisher._submit_refresh_task(
            points=points,
            compatibility=compatibility,
        )

    published = []

    class Publisher:
        def publish(self, **kwargs):
            published.append(kwargs)
            return VectorIndexArtifactLocator.locate(
                scope=kwargs["scope"],
                content_sha256=kwargs["content_sha256"],
            ).to_reference()

    service = CodeCompassVectorRetrievalService(
        repo_root=tmp_path,
        embedding_records_path="unused.json",
        manifest_path="unused-manifest.json",
        index_path="unused-index.json",
        vector_store=_RecordingStore(),
        trusted_scope=points[0].scope,
        index_task_service=TaskService(),
        index_input_publisher=Publisher(),
    )
    result = service._submit_refresh_task(
        points=points,
        compatibility=compatibility,
    )

    assert result["status"] == "queued"
    assert published[0]["scope"] == points[0].scope
    assert "points" not in submissions[-1]["payload"]
    assert submissions[-1]["payload"]["input_ref"] == (
        VectorIndexArtifactLocator.locate(
            scope=published[0]["scope"],
            content_sha256=published[0]["content_sha256"],
        ).to_reference()
    )
    assert submissions[-1]["payload"]["compatibility"]["manifest_hash"] == "manifest-a"
