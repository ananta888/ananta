from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from agent.hybrid_orchestrator import ContextChunk
from agent.services.retrieval_source_adapters import (
    WikiKnowledgeSourceAdapter,
)
from agent.services.vector_index_task_service import (
    VectorIndexTaskService,
)
from agent.services.vector_store_rollout_service import (
    InMemoryVectorStoreRolloutStore,
    VectorStoreRolloutService,
)
from agent.services.wiki_retrieval_index_service import (
    WikiRetrievalIndexService,
)
from agent.services.wiki_vector_runtime_service import (
    HubWikiVectorRuntimeResolver,
    build_default_wiki_vector_runtime_resolver,
    build_wiki_retrieval_index_service,
)
from tests.vector_index_attestation_test_support import TASK_SIGNER
from worker.retrieval.embedding_provider import HashEmbeddingProvider
from worker.retrieval.vector_index_artifact_locator import (
    VectorIndexArtifactLocator,
)
from worker.retrieval.vector_store_config import (
    AvailabilityPolicy,
    QdrantVectorStoreConfig,
)
from worker.retrieval.vector_store_contract import (
    VectorSearchHit,
    VectorSearchQuery,
    VectorSearchResult,
    VectorStoreError,
)
from worker.retrieval.vector_store_fallback import FallbackVectorSearch
from worker.retrieval.wiki_vector_store import (
    WikiPreparedVectorBackend,
    WikiVectorStore,
    WikiVectorStoreConfig,
)


class _VectorStore:
    def __init__(self, config: WikiVectorStoreConfig) -> None:
        self.config = config
        self.close_calls = 0

    def search(self, **kwargs):
        del kwargs
        return []

    def close(self) -> None:
        self.close_calls += 1


class _TaskService:
    def __init__(self) -> None:
        self.submissions: list[dict] = []

    def submit(self, **kwargs):
        self.submissions.append(kwargs)
        return {
            "job_id": f"vector-index-{kwargs['operation']}",
            "status": "queued",
        }


class _Publisher:
    def __init__(self) -> None:
        self.publications: list[dict] = []

    def publish(self, **kwargs):
        self.publications.append(kwargs)
        return VectorIndexArtifactLocator.locate(
            scope=kwargs["scope"],
            content_sha256=kwargs["content_sha256"],
        ).to_reference()


class _TaskRepository:
    def __init__(self) -> None:
        self.rows: dict[str, SimpleNamespace] = {}

    def get_by_id(self, task_id: str):
        return self.rows.get(task_id)

    def get_all(self):
        return list(self.rows.values())


class _HubTaskQueue:
    def __init__(self, repository: _TaskRepository) -> None:
        self._repository = repository

    def ingest_task(self, **kwargs):
        extra = dict(kwargs["extra_fields"])
        row = SimpleNamespace(
            id=kwargs["task_id"],
            status=kwargs["status"],
            priority=kwargs["priority"],
            updated_at=1.0,
            task_kind=extra["task_kind"],
            assigned_agent_url=None,
            worker_execution_context=extra["worker_execution_context"],
            verification_status={},
        )
        row.model_dump = lambda row=row: dict(vars(row))
        self._repository.rows[row.id] = row


class _CapturingVectorSearch:
    def __init__(self) -> None:
        self.query = None

    def search_by_vector(self, query):
        self.query = query
        return VectorSearchResult(hits=())

    def close(self) -> None:
        pass


class _WikiKnowledge:
    def search(self, _query, **_kwargs):
        return [
            ContextChunk(
                engine="knowledge_index",
                source="wiki/a",
                content="first",
                score=4.0,
                metadata={"record_id": "wiki-a"},
            ),
            ContextChunk(
                engine="knowledge_index",
                source="wiki/b",
                content="second",
                score=3.0,
                metadata={"record_id": "wiki-b"},
            ),
        ]


def test_wiki_retrieval_close_delegates_once(tmp_path):
    vector_store = _VectorStore(WikiVectorStoreConfig())
    service = WikiRetrievalIndexService(
        fts_db_path=tmp_path / "wiki.sqlite3",
        vector_index_path=tmp_path / "wiki.json",
        vector_store=vector_store,
    )

    service.close()
    service.close()

    assert vector_store.close_calls == 1


class _HybridRetrieval:
    def hybrid_search(self, _query, **_kwargs):
        return [
            {
                "record_id": "wiki-b",
                "hybrid_score": 0.9,
            },
            {
                "record_id": "wiki-a",
                "hybrid_score": 0.4,
            },
        ]


class _UnavailableVectorSearch:
    def search_by_vector(self, query):
        del query
        raise VectorStoreError("qdrant_unavailable")


class _StaticVectorSearch:
    def search_by_vector(self, query):
        del query
        return VectorSearchResult(
            hits=(
                VectorSearchHit(
                    record_id="wiki-b",
                    score=0.91,
                    payload={},
                ),
            ),
            diagnostics={"status": "ready"},
            requested_provider="json",
            effective_provider="json",
            reason="ok",
        )


class _PolicyManagedHybridRetrieval:
    def __init__(self, mode: str) -> None:
        fallback = _StaticVectorSearch() if mode == "explicit_json_fallback" else None
        self._search = FallbackVectorSearch(
            primary=_UnavailableVectorSearch(),
            fallback=fallback,
            policy=AvailabilityPolicy(mode),
            fallback_compatibility=lambda _query: True,
        )

    def hybrid_search(self, _query, *, top_k):
        result = self._search.search_by_vector(
            VectorSearchQuery(
                query_vector=(1.0,),
                top_k=top_k,
            )
        )
        return [hit.as_dict() for hit in result.hits]


def _config() -> WikiVectorStoreConfig:
    return WikiVectorStoreConfig(
        provider="qdrant",
        qdrant_enabled=True,
        collection_prefix="ananta-wiki",
        workspace_id="workspace-a",
        source_id="wiki-source-a",
        profile_name="default",
        qdrant=QdrantVectorStoreConfig(collection_prefix="ananta-wiki"),
    )


def test_wiki_qdrant_refresh_delegates_embedding_and_write_to_worker(
    tmp_path,
) -> None:
    task_service = _TaskService()
    publisher = _Publisher()
    service = WikiRetrievalIndexService(
        fts_db_path=tmp_path / "wiki.db",
        vector_index_path=tmp_path / "wiki.json",
        vector_store=_VectorStore(_config()),
        vector_config=_config(),
        index_task_service=task_service,
        index_input_publisher=publisher,
        allow_hub_qdrant_reads=True,
    )

    result = service.refresh(
        documents=[
            {
                "record_id": "wiki-a",
                "embedding_text": "bounded wiki section",
                "kind": "wiki_section_chunk",
                "file": "wiki/a",
                "source_scope": "wiki",
            }
        ],
        retrieval_cache_state="cache-a",
        manifest_hash="manifest-a",
    )

    assert result["status"] == "queued"
    submitted = task_service.submissions[0]
    assert submitted["operation"] == "refresh"
    assert submitted["trusted_scope"].domain == "wiki"
    assert submitted["payload"]["preparation"]["kind"] == "wiki_documents"
    assert "points" not in submitted["payload"]
    artifact = json.loads(publisher.publications[0]["content"].decode("utf-8"))
    assert artifact["kind"] == "wiki_documents"
    assert artifact["documents"][0]["embedding_text"]
    assert all("vector" not in row for row in artifact["documents"])


def test_wiki_qdrant_delete_is_hub_owned_task(tmp_path) -> None:
    task_service = _TaskService()
    service = WikiRetrievalIndexService(
        fts_db_path=tmp_path / "wiki.db",
        vector_index_path=tmp_path / "wiki.json",
        vector_store=_VectorStore(_config()),
        vector_config=_config(),
        index_task_service=task_service,
        index_input_publisher=_Publisher(),
        allow_hub_qdrant_reads=True,
    )

    result = service.delete(record_ids=["wiki-a"])

    assert result["status"] == "queued"
    assert task_service.submissions[0]["operation"] == "delete"
    assert task_service.submissions[0]["payload"] == {"point_ids": ["wiki-a"]}


def test_wiki_runtime_resolves_independent_qdrant_rollout() -> None:
    rollout = VectorStoreRolloutService(
        store=InMemoryVectorStoreRolloutStore(),
        global_config={},
        audit=lambda _event, _payload: None,
    )
    rollout.set_workspace_override(
        domain="wiki",
        workspace_id="workspace-a",
        override={
            "provider": "qdrant",
            "availability": {
                "on_unavailable": "fail_fast",
            },
        },
        expected_revision=0,
        actor="admin-a",
    )
    resolver = HubWikiVectorRuntimeResolver(
        workspace_id="workspace-a",
        source_id="wiki-source-a",
        rollout_service=rollout,
        index_task_service=_TaskService(),
        index_input_publisher=_Publisher(),
        allow_hub_qdrant_reads=True,
    )

    runtime = resolver.resolve()

    assert runtime.vector_store_config.provider == "qdrant"
    assert runtime.vector_store_config.collection_prefix == "ananta-wiki"
    assert runtime.vector_store_config.vector_scope().domain == "wiki"
    assert runtime.vector_store_config.availability.on_unavailable.value == "fail_fast"


def test_completed_delegated_refresh_advances_wiki_read_state_without_restart(
    tmp_path,
) -> None:
    repository = _TaskRepository()
    queue = _HubTaskQueue(repository)
    clock = iter((10.0, 20.0, 30.0))
    rollout = VectorStoreRolloutService(
        store=InMemoryVectorStoreRolloutStore(),
        global_config={},
        audit=lambda _event, _payload: None,
    )
    rollout.set_workspace_override(
        domain="wiki",
        workspace_id="workspace-a",
        override={"provider": "qdrant"},
        expected_revision=0,
        actor="admin-a",
    )

    def update(task_id, status, **kwargs):
        row = repository.rows[task_id]
        row.status = status
        row.updated_at = next(clock)
        if "verification_status" in kwargs:
            row.verification_status = kwargs["verification_status"]
        if "worker_execution_context" in kwargs:
            row.worker_execution_context = kwargs[
                "worker_execution_context"
            ]

    tasks = VectorIndexTaskService(
        task_queue=queue,
        task_repository=repository,
        rollout_service=rollout,
        status_updater=update,
        audit=lambda _event, _payload: None,
        clock=lambda: 5.0,
        task_signer=TASK_SIGNER,
    )
    publisher = _Publisher()
    resolver = HubWikiVectorRuntimeResolver(
        workspace_id="workspace-a",
        source_id="wiki-source-a",
        rollout_service=rollout,
        index_task_service=tasks,
        index_input_publisher=publisher,
        allow_hub_qdrant_reads=True,
    )
    service = WikiRetrievalIndexService(
        fts_db_path=tmp_path / "wiki.db",
        vector_index_path=tmp_path / "wiki.json",
        vector_store=_VectorStore(_config()),
        vector_config=_config(),
        index_task_service=tasks,
        index_input_publisher=publisher,
        allow_hub_qdrant_reads=True,
    )
    original_signature = resolver.cache_signature()
    queued = service.refresh(
        documents=[
            {
                "record_id": "wiki-a",
                "embedding_text": "bounded wiki section",
                "kind": "wiki_section_chunk",
                "file": "wiki/a",
                "source_scope": "wiki",
            }
        ],
        retrieval_cache_state="cache-completed",
        manifest_hash="manifest-completed",
    )

    assert resolver.cache_signature() == original_signature
    worker_audience = "http://worker-a:5000"
    repository.rows[
        queued["job_id"]
    ].assigned_agent_url = worker_audience
    dispatched = tasks.issue_dispatch_attempt(
        job_id=queued["job_id"],
        worker_audience=worker_audience,
        phase="execute",
    )
    tasks.admit_dispatch_attempt(
        job_id=queued["job_id"],
        attempt_id=dispatched["dispatch"]["attempt_id"],
        sequence=dispatched["dispatch"]["sequence"],
        phase="execute",
        worker_audience=worker_audience,
    )
    tasks.accept_worker_result(
        job_id=queued["job_id"],
        result={
            "schema": "ananta.vector_index_task_result.v1",
            "job_id": queued["job_id"],
            "attempt_id": dispatched["dispatch"]["attempt_id"],
            "idempotency_key": queued["idempotency_key"],
            "operation": "refresh",
            "status": "completed",
            "reason_code": "refreshed",
            "diagnostics": {},
            "result": {"indexed_documents": 1},
            "error": None,
        },
    )

    runtime = resolver.resolve()
    config = runtime.vector_store_config
    assert resolver.cache_signature() != original_signature
    assert config.retrieval_cache_state == "cache-completed"
    assert config.manifest_hash == "manifest-completed"
    assert config.expected_compatibility is not None
    capture = _CapturingVectorSearch()
    store = WikiVectorStore(
        backend=WikiPreparedVectorBackend(capture, config),
        config=config,
    )
    store.search(
        query="bounded query",
        embedding_provider=HashEmbeddingProvider(
            provider_id="wiki_local_hash",
            model_version="wiki-hash-v1",
            dimensions=12,
        ),
        top_k=2,
    )
    assert capture.query is not None
    assert capture.query.compatibility == config.expected_compatibility


def test_wiki_default_runtime_requires_complete_trusted_scope() -> None:
    assert build_default_wiki_vector_runtime_resolver(environ={}) is None

    try:
        build_default_wiki_vector_runtime_resolver(environ={"ANANTA_WIKI_VECTOR_WORKSPACE_ID": "workspace-a"})
    except ValueError as exc:
        assert str(exc) == "wiki_vector_runtime_scope_incomplete"
    else:
        raise AssertionError("incomplete Wiki scope unexpectedly accepted")


def test_productive_wiki_builder_keeps_json_as_default(tmp_path) -> None:
    rollout = VectorStoreRolloutService(
        store=InMemoryVectorStoreRolloutStore(),
        global_config={},
        audit=lambda _event, _payload: None,
    )
    resolver = HubWikiVectorRuntimeResolver(
        workspace_id="workspace-a",
        source_id="wiki-source-a",
        rollout_service=rollout,
    )

    service = build_wiki_retrieval_index_service(
        fts_db_path=tmp_path / "wiki.db",
        vector_index_path=tmp_path / "wiki.json",
        runtime_resolver=resolver,
    )

    assert service._vector_config.provider == "json"


def test_productive_wiki_adapter_reranks_hydrated_chunks() -> None:
    adapter = WikiKnowledgeSourceAdapter(
        _WikiKnowledge(),
        hybrid_retrieval_provider=lambda: _HybridRetrieval(),
    )

    result = adapter.search("bounded query", top_k=2)

    assert [chunk.metadata["record_id"] for chunk in result] == [
        "wiki-b",
        "wiki-a",
    ]
    assert result[0].content == "second"
    assert result[0].metadata["wiki_vector_rank"] == 1
    assert result[0].metadata["wiki_vector_score"] == 0.9


def test_productive_wiki_adapter_degrades_to_knowledge_search() -> None:
    def unavailable():
        raise RuntimeError("backend unavailable")

    adapter = WikiKnowledgeSourceAdapter(
        _WikiKnowledge(),
        hybrid_retrieval_provider=unavailable,
    )

    result = adapter.search("bounded query", top_k=1)

    assert [chunk.metadata["record_id"] for chunk in result] == ["wiki-a"]


def test_qdrant_fail_fast_reaches_wiki_product_caller() -> None:
    adapter = WikiKnowledgeSourceAdapter(
        _WikiKnowledge(),
        hybrid_retrieval_provider=lambda: _PolicyManagedHybridRetrieval("fail_fast"),
    )

    with pytest.raises(
        VectorStoreError,
        match="qdrant_unavailable",
    ):
        adapter.search("bounded query", top_k=2)


def test_qdrant_degraded_empty_keeps_wiki_lexical_results() -> None:
    adapter = WikiKnowledgeSourceAdapter(
        _WikiKnowledge(),
        hybrid_retrieval_provider=lambda: _PolicyManagedHybridRetrieval("degraded_empty"),
    )

    result = adapter.search("bounded query", top_k=2)

    assert [chunk.metadata["record_id"] for chunk in result] == [
        "wiki-a",
        "wiki-b",
    ]


def test_explicit_json_fallback_reranks_wiki_product_results() -> None:
    adapter = WikiKnowledgeSourceAdapter(
        _WikiKnowledge(),
        hybrid_retrieval_provider=lambda: _PolicyManagedHybridRetrieval("explicit_json_fallback"),
    )

    result = adapter.search("bounded query", top_k=2)

    assert [chunk.metadata["record_id"] for chunk in result] == [
        "wiki-b",
        "wiki-a",
    ]
    assert result[0].metadata["wiki_vector_rank"] == 1
