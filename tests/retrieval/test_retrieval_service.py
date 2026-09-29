from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from types import SimpleNamespace

from agent.hybrid_orchestrator import ContextChunk
from agent.services.retrieval_service import RetrievalService
from agent.services.retrieval_vector_runtime_scope_service import (
    HubRetrievalVectorRuntimeResolverFactory,
    RetrievalVectorRuntimeScope,
)
from worker.retrieval.vector_store_contract import VectorScope


class _FakeContextManager:
    policy_version = "v1"

    def rerank(self, *, chunks, query, max_chunks, max_chars, max_tokens):
        del query, max_chars, max_tokens
        return sorted(chunks, key=lambda chunk: chunk.score, reverse=True)[:max_chunks]

    def estimate_tokens(self, text: str) -> int:
        return len(text.split())


class _FakeOrchestrator:
    def __init__(self) -> None:
        self.context_manager = _FakeContextManager()

    def _redact(self, text: str) -> str:
        return text

    def get_relevant_context(self, query: str, *, domain_scope=None) -> dict[str, object]:
        return {
            "query": query,
            "strategy": {"repository_map": 1},
            "policy_version": "v1",
            "chunks": [
                {
                    "engine": "repository_map",
                    "source": "README.md",
                    "score": 1.0,
                    "content": "repo context",
                    "metadata": {},
                }
            ],
            "context_text": "[repository_map] README.md\nrepo context",
            "token_estimate": 4,
        }


class _FakeRedactingOrchestrator(_FakeOrchestrator):
    def _redact(self, text: str) -> str:
        return str(text or "").replace("sk-secret-token-1234567890", "[REDACTED]")

    def get_relevant_context(self, query: str, *, domain_scope=None) -> dict[str, object]:
        return {
            "query": query,
            "strategy": {"repository_map": 1, "raw_query": "sk-secret-token-1234567890"},
            "policy_version": "v1",
            "chunks": [
                {
                    "engine": "repository_map",
                    "source": "secrets/sk-secret-token-1234567890.txt",
                    "score": 1.0,
                    "content": "api key sk-secret-token-1234567890",
                    "metadata": {"token_hint": "sk-secret-token-1234567890"},
                }
            ],
            "context_text": (
                "[repository_map] secrets/sk-secret-token-1234567890.txt\napi key sk-secret-token-1234567890"
            ),
            "token_estimate": 4,
        }


class _FakeKnowledgeIndexRetrievalService:
    def __init__(self) -> None:
        self.scope_calls: list[set[str]] = []

    def search(self, query: str, *, top_k: int, task_kind=None, retrieval_intent=None, source_scopes=None):
        self.last_top_k = top_k
        self.last_task_kind = task_kind
        self.last_retrieval_intent = retrieval_intent
        self.last_source_scopes = set(source_scopes or [])
        self.scope_calls.append(set(source_scopes or []))
        del query
        return [
            ContextChunk(
                engine="knowledge_index",
                source="docs/payment-timeouts.md",
                content="knowledge timeout context",
                score=2.0,
                metadata={"knowledge_index_id": "idx-1"},
            )
        ]


class _FakeMemoryEntryRepo:
    def __init__(self) -> None:
        self._by_task = {}
        self._by_goal = {}

    def get_by_task(self, task_id: str):
        return list(self._by_task.get(task_id, []))

    def get_by_goal(self, goal_id: str):
        return list(self._by_goal.get(goal_id, []))


def test_retrieval_service_merges_knowledge_index_chunks():
    knowledge = _FakeKnowledgeIndexRetrievalService()
    service = RetrievalService(
        knowledge_index_retrieval_service=knowledge, memory_entry_repository=_FakeMemoryEntryRepo()
    )
    service._orchestrator = _FakeOrchestrator()
    service._signature = service._config_signature()

    payload = service.retrieve_context("timeout")

    assert payload["strategy"]["repository_map"] == 1
    assert payload["strategy"]["knowledge_index"] == 1
    assert payload["strategy"]["knowledge_index_reason"] == "default_balanced_query"
    assert [chunk["engine"] for chunk in payload["chunks"]] == ["knowledge_index", "repository_map"]
    assert "knowledge timeout context" in payload["context_text"]
    assert knowledge.last_top_k >= 1
    assert payload["strategy"]["fusion"]["mode"] == "deterministic_v2"
    assert payload["strategy"]["fusion"]["candidate_counts"]["knowledge_index"] == 1
    assert knowledge.last_source_scopes == {"artifact"}
    assert isinstance(payload["strategy"]["fusion"]["final_ranked_sources"], list)
    assert [stage["stage"] for stage in payload["strategy"]["fusion"]["selection_stages"]] == [
        "all_candidates",
        "deduped",
        "expanded",
        "reranked",
        "diversified",
        "final",
    ]


def test_retrieval_service_prefers_more_knowledge_context_for_doc_queries():
    knowledge = _FakeKnowledgeIndexRetrievalService()
    service = RetrievalService(
        knowledge_index_retrieval_service=knowledge, memory_entry_repository=_FakeMemoryEntryRepo()
    )
    service._orchestrator = _FakeOrchestrator()
    service._signature = service._config_signature()

    payload = service.retrieve_context("architecture docs overview")

    assert "query_doc_or_architecture" in payload["strategy"]["knowledge_index_reason"]
    assert knowledge.last_top_k == service._config_signature()[5]


def test_retrieval_service_propagates_task_aware_hints():
    knowledge = _FakeKnowledgeIndexRetrievalService()
    service = RetrievalService(
        knowledge_index_retrieval_service=knowledge, memory_entry_repository=_FakeMemoryEntryRepo()
    )
    service._orchestrator = _FakeOrchestrator()
    service._signature = service._config_signature()

    payload = service.retrieve_context(
        "investigate timeout",
        task_kind="bugfix",
        retrieval_intent="localize bug",
    )

    assert knowledge.last_task_kind == "bugfix"
    assert knowledge.last_retrieval_intent == "localize bug"
    assert "task_kind_code_or_debug" in payload["strategy"]["knowledge_index_reason"]
    assert payload["strategy"]["fusion"]["task_kind"] == "bugfix"


def test_retrieval_service_includes_result_memory_for_neighbor_tasks():
    knowledge = _FakeKnowledgeIndexRetrievalService()
    memory_repo = _FakeMemoryEntryRepo()
    memory_repo._by_task["task-parent-1"] = [
        type(
            "Entry",
            (),
            {
                "id": "mem-1",
                "task_id": "task-parent-1",
                "title": "Fix timeout handling",
                "summary": "Updated timeout retry logic and tests",
                "content": "Updated timeout retry logic and tests for worker pipeline",
                "retrieval_tags": ["bugfix", "completed"],
                "entry_type": "worker_result",
                "memory_metadata": {"compacted_summary": "retry logic | tests"},
            },
        )()
    ]
    service = RetrievalService(knowledge_index_retrieval_service=knowledge, memory_entry_repository=memory_repo)
    service._orchestrator = _FakeOrchestrator()
    service._signature = service._config_signature()

    payload = service.retrieve_context(
        "timeout retry",
        task_kind="bugfix",
        task_id="task-parent-1",
        neighbor_task_ids=["task-parent-2"],
    )

    assert payload["strategy"]["result_memory"] == 1
    assert payload["strategy"]["result_memory_reason"] == "ok"
    assert "result_memory" in [chunk["engine"] for chunk in payload["chunks"]]


def test_retrieval_service_prefers_structured_result_memory_document():
    knowledge = _FakeKnowledgeIndexRetrievalService()
    memory_repo = _FakeMemoryEntryRepo()
    memory_repo._by_task["task-parent-1"] = [
        type(
            "Entry",
            (),
            {
                "id": "mem-2",
                "task_id": "task-parent-1",
                "title": "Refactor parser",
                "summary": "Parser refactoring done",
                "content": "Long raw content that should be superseded",
                "retrieval_tags": ["refactor", "completed"],
                "entry_type": "worker_result",
                "memory_metadata": {
                    "retrieval_document": (
                        "summary: parser refactor\n"
                        "changed_files: app/parser.py\n"
                        "tests: passed_signal=True; failed_signal=False"
                    ),
                    "structured_summary": {"focus_terms": ["parser", "refactor"]},
                    "memory_format": "worker_result_compact_v2",
                },
            },
        )()
    ]
    service = RetrievalService(knowledge_index_retrieval_service=knowledge, memory_entry_repository=memory_repo)
    service._orchestrator = _FakeOrchestrator()
    service._signature = service._config_signature()

    payload = service.retrieve_context("parser refactor", task_id="task-parent-1")

    memory_chunk = next(chunk for chunk in payload["chunks"] if chunk["engine"] == "result_memory")
    assert "changed_files: app/parser.py" in memory_chunk["content"]
    assert (memory_chunk.get("metadata") or {}).get("memory_format") == "worker_result_compact_v2"


def test_retrieval_service_propagates_security_metadata_from_memory_entries():
    knowledge = _FakeKnowledgeIndexRetrievalService()
    memory_repo = _FakeMemoryEntryRepo()
    memory_repo._by_task["task-sec-1"] = [
        type(
            "Entry",
            (),
            {
                "id": "mem-sec-1",
                "task_id": "task-sec-1",
                "title": "Security-sensitive memory",
                "summary": "Contains operations context",
                "content": "sensitive execution trace",
                "retrieval_tags": ["security"],
                "entry_type": "worker_result",
                "memory_metadata": {
                    "retrieval_document": "security summary",
                    "compacted_summary": "security compact",
                    "security_metadata": {
                        "classification": "restricted",
                        "source_origin": "task_memory",
                        "sensitivity": "internal_high",
                        "tenancy": "tenant_alpha",
                        "approval_class": "operator_review",
                        "chunk_security_tags": ["tenant:alpha", "security"],
                    },
                },
            },
        )()
    ]
    service = RetrievalService(knowledge_index_retrieval_service=knowledge, memory_entry_repository=memory_repo)
    service._orchestrator = _FakeOrchestrator()
    service._signature = service._config_signature()

    payload = service.retrieve_context("security summary", task_id="task-sec-1")

    memory_chunk = next(chunk for chunk in payload["chunks"] if chunk["engine"] == "result_memory")
    metadata = dict(memory_chunk.get("metadata") or {})
    security = dict(metadata.get("security_metadata") or {})
    assert security.get("classification") == "restricted"
    assert security.get("sensitivity") == "internal_high"
    assert security.get("tenancy") == "tenant_alpha"
    assert metadata.get("classification") == "restricted"
    assert metadata.get("sensitivity") == "internal_high"
    assert metadata.get("chunk_security_tags") == ["tenant:alpha", "security"]


def test_retrieval_service_redacts_sensitive_debug_fields_in_strategy_and_sources():
    knowledge = _FakeKnowledgeIndexRetrievalService()
    service = RetrievalService(
        knowledge_index_retrieval_service=knowledge, memory_entry_repository=_FakeMemoryEntryRepo()
    )
    service._orchestrator = _FakeRedactingOrchestrator()
    service._signature = service._config_signature()

    payload = service.retrieve_context("expose sk-secret-token-1234567890")

    assert "sk-secret-token-1234567890" not in payload["context_text"]
    assert "sk-secret-token-1234567890" not in str(payload["strategy"])
    for chunk in payload["chunks"]:
        assert "sk-secret-token-1234567890" not in str(chunk.get("source") or "")
        assert "sk-secret-token-1234567890" not in str(chunk.get("metadata") or {})


def test_retrieval_service_selection_stage_trace_stays_deterministic():
    knowledge = _FakeKnowledgeIndexRetrievalService()
    service = RetrievalService(
        knowledge_index_retrieval_service=knowledge, memory_entry_repository=_FakeMemoryEntryRepo()
    )
    service._orchestrator = _FakeOrchestrator()
    service._signature = service._config_signature()

    payload = service.retrieve_context("timeout")
    selection_stages = payload["strategy"]["fusion"]["selection_stages"]

    assert selection_stages[0]["stage"] == "all_candidates"
    assert selection_stages[-1]["stage"] == "final"
    assert selection_stages[-1]["count"] == payload["strategy"]["fusion"]["candidate_counts"]["final"]
    assert selection_stages[-1]["top"][0]["engine"] == payload["chunks"][0]["engine"]


def test_retrieval_service_supports_repo_only_source_filter():
    knowledge = _FakeKnowledgeIndexRetrievalService()
    service = RetrievalService(
        knowledge_index_retrieval_service=knowledge, memory_entry_repository=_FakeMemoryEntryRepo()
    )
    service._orchestrator = _FakeOrchestrator()
    service._signature = service._config_signature()

    payload = service.retrieve_context("timeout", source_types=["repo"])

    assert payload["strategy"]["source_policy"]["effective"] == ["repo"]
    assert payload["strategy"]["knowledge_index"] == 0
    assert payload["strategy"]["result_memory"] == 0
    assert [chunk["engine"] for chunk in payload["chunks"]] == ["repository_map"]


def test_retrieval_service_marks_pre_catalog_source_id_as_unverified():
    knowledge = _FakeKnowledgeIndexRetrievalService()
    service = RetrievalService(
        knowledge_index_retrieval_service=knowledge, memory_entry_repository=_FakeMemoryEntryRepo()
    )
    service._orchestrator = _FakeOrchestrator()
    service._signature = service._config_signature()

    payload = service.retrieve_context("timeout")

    for chunk in payload["chunks"]:
        metadata = dict(chunk.get("metadata") or {})
        assert metadata.get("source_type")
        assert metadata.get("chunk_id")
        assert metadata.get("source_id_verified") is False
        verification = dict(metadata.get("source_id_verification") or {})
        assert verification.get("status") == "unverified"
        assert verification.get("reason_code") in {
            "source_id_missing",
            "source_id_unverified",
        }
        citation = dict(metadata.get("citation") or {})
        assert citation.get("source_type") == metadata.get("source_type")
        assert citation.get("source_id") is None
        assert citation.get("verification_status") == "unverified"

    repo_chunk = next(chunk for chunk in payload["chunks"] if chunk["engine"] == "repository_map")
    assert (repo_chunk.get("metadata") or {}).get("source_id") is None


def test_retrieval_service_exposes_source_type_contributions_in_fusion_trace():
    knowledge = _FakeKnowledgeIndexRetrievalService()
    service = RetrievalService(
        knowledge_index_retrieval_service=knowledge, memory_entry_repository=_FakeMemoryEntryRepo()
    )
    service._orchestrator = _FakeOrchestrator()
    service._signature = service._config_signature()

    payload = service.retrieve_context("timeout")
    fusion = dict((payload.get("strategy") or {}).get("fusion") or {})

    assert "source_type_contributions_before" in fusion
    assert "source_type_contributions_after_dedupe" in fusion
    assert "source_type_contributions_final" in fusion
    assert "repo" in dict(fusion.get("source_type_contributions_before") or {})


def test_retrieval_service_preflight_reports_source_diagnostics():
    knowledge = _FakeKnowledgeIndexRetrievalService()
    knowledge.get_source_preflight = lambda: {
        "artifact": {"status": "degraded", "completed_indices": 0, "issues": ["no_completed_indices"]},
        "wiki": {"status": "degraded", "completed_indices": 0, "issues": ["no_completed_indices"]},
    }
    service = RetrievalService(
        knowledge_index_retrieval_service=knowledge, memory_entry_repository=_FakeMemoryEntryRepo()
    )
    service._orchestrator = _FakeOrchestrator()
    service._signature = service._config_signature()

    preflight = service.get_source_preflight()

    assert preflight["status"] in {"ok", "degraded", "error"}
    assert "sources" in preflight
    assert "repo" in preflight["sources"]
    assert "artifact" in preflight["sources"]


def test_retrieval_service_smoke_repo_and_wiki_sources_preserve_citations(monkeypatch):
    class _WikiKnowledge(_FakeKnowledgeIndexRetrievalService):
        def search(self, query: str, *, top_k: int, task_kind=None, retrieval_intent=None, source_scopes=None):
            self.last_top_k = top_k
            self.last_task_kind = task_kind
            self.last_retrieval_intent = retrieval_intent
            self.last_source_scopes = set(source_scopes or [])
            self.scope_calls.append(set(source_scopes or []))
            del query
            scope = next(iter(self.last_source_scopes), "artifact")
            if scope == "wiki":
                return [
                    ContextChunk(
                        engine="knowledge_index",
                        source="wiki/payment.md",
                        content="Wiki timeout guidance",
                        score=1.9,
                        metadata={
                            "source_scope": "wiki",
                            "article_title": "Payment retries",
                            "section_title": "Timeout handling",
                            "language": "en",
                        },
                    )
                ]
            return []

    monkeypatch.setattr("agent.services.retrieval_service.settings.rag_source_wiki_enabled", True)
    knowledge = _WikiKnowledge()
    service = RetrievalService(
        knowledge_index_retrieval_service=knowledge, memory_entry_repository=_FakeMemoryEntryRepo()
    )
    service._orchestrator = _FakeOrchestrator()
    service._signature = service._config_signature()

    payload = service.retrieve_context("timeout", source_types=["repo", "wiki"])

    source_types = {dict(chunk.get("metadata") or {}).get("source_type") for chunk in payload["chunks"]}
    assert "repo" in source_types
    assert "wiki" in source_types
    wiki_chunk = next(
        chunk for chunk in payload["chunks"] if dict(chunk.get("metadata") or {}).get("source_type") == "wiki"
    )
    wiki_citation = dict((wiki_chunk.get("metadata") or {}).get("citation") or {})
    assert wiki_citation.get("article_title") == "Payment retries"
    assert wiki_citation.get("section_title") == "Timeout handling"
    assert {"wiki"} in knowledge.scope_calls


def test_retrieval_service_emits_codecompass_retrieval_trace_shape():
    knowledge = _FakeKnowledgeIndexRetrievalService()
    service = RetrievalService(
        knowledge_index_retrieval_service=knowledge, memory_entry_repository=_FakeMemoryEntryRepo()
    )
    service._orchestrator = _FakeOrchestrator()
    service._signature = service._config_signature()

    payload = service.retrieve_context("timeout")

    trace = dict(payload.get("retrieval_trace") or {})
    assert trace.get("trace_id")
    assert trace.get("context_hash")
    assert trace.get("selected_chunk_counts_by_channel")
    assert trace.get("final_chunk_count") == len(payload.get("chunks") or [])
    assert trace.get("enabled_channels")
    assert isinstance(trace.get("degraded_channels"), list)
    strategy_trace = dict((payload.get("strategy") or {}).get("retrieval_trace") or {})
    assert strategy_trace.get("trace_id") == trace.get("trace_id")


def test_retrieval_service_productively_wires_wiki_vector_runtime(
    monkeypatch,
):
    class _WikiResolver:
        def cache_signature(self):
            return ("wiki-config-v1",)

    class _WikiKnowledge(_FakeKnowledgeIndexRetrievalService):
        def search(self, query, **kwargs):
            del query, kwargs
            return [
                ContextChunk(
                    engine="knowledge_index",
                    source="wiki/a",
                    content="first",
                    score=2.0,
                    metadata={"record_id": "wiki-a"},
                ),
                ContextChunk(
                    engine="knowledge_index",
                    source="wiki/b",
                    content="second",
                    score=1.0,
                    metadata={"record_id": "wiki-b"},
                ),
            ]

    class _WikiVectorRetrieval:
        def hybrid_search(self, query, **kwargs):
            del query, kwargs
            return [
                {
                    "record_id": "wiki-b",
                    "hybrid_score": 0.9,
                }
            ]

    built = []

    def _build(**kwargs):
        built.append(kwargs)
        return _WikiVectorRetrieval()

    monkeypatch.setattr(
        "agent.services.retrieval_service.build_wiki_retrieval_index_service",
        _build,
    )
    service = RetrievalService(
        knowledge_index_retrieval_service=_WikiKnowledge(),
        memory_entry_repository=_FakeMemoryEntryRepo(),
        wiki_vector_runtime_resolver=_WikiResolver(),
    )

    chunks = service._source_adapters["wiki"].search(
        "query",
        top_k=2,
    )

    assert [chunk.metadata["record_id"] for chunk in chunks] == [
        "wiki-b",
        "wiki-a",
    ]
    assert len(built) == 1


class _CloseSpyOrchestrator(_FakeOrchestrator):
    def __init__(self, workspace_id: str = "") -> None:
        super().__init__()
        self.workspace_id = workspace_id
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1

    def get_relevant_context(
        self,
        query: str,
        *,
        domain_scope=None,
    ) -> dict[str, object]:
        del domain_scope
        source = f"{self.workspace_id}/README.md" if self.workspace_id else "README.md"
        return {
            "query": query,
            "strategy": {"repository_map": 1},
            "policy_version": "v1",
            "chunks": [
                {
                    "engine": "repository_map",
                    "source": source,
                    "score": 1.0,
                    "content": f"context:{self.workspace_id}",
                    "metadata": {},
                }
            ],
            "context_text": f"context:{self.workspace_id}",
            "token_estimate": 1,
        }


class _ScopedRuntimeResolver:
    def __init__(
        self,
        scope: RetrievalVectorRuntimeScope,
        revisions: dict[str, int],
    ) -> None:
        self.scope = scope
        self._revisions = revisions

    def cache_signature(self, **_kwargs):
        return (
            str(self._revisions[self.scope.workspace_id]),
            self.scope.workspace_id,
            self.scope.profile_name,
        )


class _ScopedRuntimeResolverFactory:
    def __init__(self) -> None:
        self.revisions = {
            "workspace-a": 1,
            "workspace-b": 1,
        }

    def codecompass_resolver(
        self,
        scope: RetrievalVectorRuntimeScope,
    ):
        return _ScopedRuntimeResolver(scope, self.revisions)

    def wiki_resolver(
        self,
        scope: RetrievalVectorRuntimeScope,
    ):
        return _ScopedRuntimeResolver(scope, self.revisions)


def test_orchestrator_rollout_swap_closes_old_runtime_once(
    monkeypatch,
):
    factory = _ScopedRuntimeResolverFactory()
    scope = RetrievalVectorRuntimeScope(
        workspace_id="workspace-a",
        codecompass_repository_id="repo-a",
    )
    built: list[_CloseSpyOrchestrator] = []
    service = RetrievalService(
        knowledge_index_retrieval_service=(_FakeKnowledgeIndexRetrievalService()),
        memory_entry_repository=_FakeMemoryEntryRepo(),
        vector_runtime_resolver_factory=factory,
    )

    def _build(runtime_resolver):
        runtime = _CloseSpyOrchestrator(runtime_resolver.scope.workspace_id)
        built.append(runtime)
        return runtime

    monkeypatch.setattr(service, "_build_orchestrator", _build)

    first = service.get_orchestrator(vector_runtime_scope=scope)
    factory.revisions["workspace-a"] = 2
    second = service.get_orchestrator(vector_runtime_scope=scope)
    again = service.get_orchestrator(vector_runtime_scope=scope)

    assert first is built[0]
    assert second is again is built[1]
    assert first.close_calls == 1
    assert second.close_calls == 0
    assert len(built) == 2


def test_orchestrator_rollout_swap_defers_close_until_request_releases_lease(
    monkeypatch,
):
    entered = Event()
    release = Event()

    class _BlockingOrchestrator(_CloseSpyOrchestrator):
        def get_relevant_context(self, query: str, *, domain_scope=None):
            entered.set()
            assert release.wait(timeout=5)
            return super().get_relevant_context(
                query,
                domain_scope=domain_scope,
            )

    factory = _ScopedRuntimeResolverFactory()
    scope = RetrievalVectorRuntimeScope(
        workspace_id="workspace-a",
        codecompass_repository_id="repo-a",
    )
    built: list[_CloseSpyOrchestrator] = []
    service = RetrievalService(
        knowledge_index_retrieval_service=(_FakeKnowledgeIndexRetrievalService()),
        memory_entry_repository=_FakeMemoryEntryRepo(),
        vector_runtime_resolver_factory=factory,
    )

    def _build(runtime_resolver):
        runtime = (
            _BlockingOrchestrator(runtime_resolver.scope.workspace_id)
            if not built
            else _CloseSpyOrchestrator(runtime_resolver.scope.workspace_id)
        )
        built.append(runtime)
        return runtime

    monkeypatch.setattr(service, "_build_orchestrator", _build)

    with ThreadPoolExecutor(max_workers=1) as executor:
        request = executor.submit(
            service.retrieve_context,
            "scope",
            source_types=["repo"],
            vector_runtime_scope=scope,
        )
        assert entered.wait(timeout=5)
        factory.revisions["workspace-a"] = 2
        replacement = service.get_orchestrator(vector_runtime_scope=scope)

        assert replacement is built[1]
        assert built[0].close_calls == 0
        release.set()
        payload = request.result(timeout=5)

    assert payload["chunks"][0]["source"].startswith("workspace-a/")
    assert built[0].close_calls == 1
    assert built[1].close_calls == 0


def test_concurrent_orchestrator_cache_miss_builds_once(
    monkeypatch,
):
    factory = _ScopedRuntimeResolverFactory()
    scope = RetrievalVectorRuntimeScope(
        workspace_id="workspace-a",
        codecompass_repository_id="repo-a",
    )
    built: list[_CloseSpyOrchestrator] = []
    service = RetrievalService(
        knowledge_index_retrieval_service=(_FakeKnowledgeIndexRetrievalService()),
        memory_entry_repository=_FakeMemoryEntryRepo(),
        vector_runtime_resolver_factory=factory,
    )

    def _build(runtime_resolver):
        runtime = _CloseSpyOrchestrator(runtime_resolver.scope.workspace_id)
        built.append(runtime)
        return runtime

    monkeypatch.setattr(service, "_build_orchestrator", _build)

    with ThreadPoolExecutor(max_workers=8) as executor:
        runtimes = list(
            executor.map(
                lambda _: service.get_orchestrator(vector_runtime_scope=scope),
                range(24),
            )
        )

    assert len(built) == 1
    assert all(runtime is built[0] for runtime in runtimes)


def test_request_scoped_product_retrieval_never_reuses_other_workspace(
    monkeypatch,
):
    factory = _ScopedRuntimeResolverFactory()
    scope_a = RetrievalVectorRuntimeScope(
        workspace_id="workspace-a",
        codecompass_repository_id="repo-a",
    )
    scope_b = RetrievalVectorRuntimeScope(
        workspace_id="workspace-b",
        codecompass_repository_id="repo-b",
    )
    built: list[_CloseSpyOrchestrator] = []
    service = RetrievalService(
        knowledge_index_retrieval_service=(_FakeKnowledgeIndexRetrievalService()),
        memory_entry_repository=_FakeMemoryEntryRepo(),
        vector_runtime_resolver_factory=factory,
    )

    def _build(runtime_resolver):
        runtime = _CloseSpyOrchestrator(runtime_resolver.scope.workspace_id)
        built.append(runtime)
        return runtime

    monkeypatch.setattr(service, "_build_orchestrator", _build)

    payload_a = service.retrieve_context(
        "scope",
        source_types=["repo"],
        vector_runtime_scope=scope_a,
    )
    payload_b = service.retrieve_context(
        "scope",
        source_types=["repo"],
        vector_runtime_scope=scope_b,
    )
    payload_a_again = service.retrieve_context(
        "scope",
        source_types=["repo"],
        vector_runtime_scope=scope_a,
    )

    assert payload_a["chunks"][0]["source"].startswith("workspace-a/")
    assert payload_b["chunks"][0]["source"].startswith("workspace-b/")
    assert payload_a_again["chunks"][0]["source"].startswith("workspace-a/")
    assert len(built) == 2
    assert all(runtime.close_calls == 0 for runtime in built)


def test_wiki_rollout_swap_is_scoped_and_closes_only_old_generation(
    monkeypatch,
):
    class _WikiRuntime:
        def __init__(self, workspace_id: str) -> None:
            self.workspace_id = workspace_id
            self.close_calls = 0

        def close(self) -> None:
            self.close_calls += 1

    factory = _ScopedRuntimeResolverFactory()
    scope_a = RetrievalVectorRuntimeScope(
        workspace_id="workspace-a",
        wiki_source_id="wiki-a",
    )
    scope_b = RetrievalVectorRuntimeScope(
        workspace_id="workspace-b",
        wiki_source_id="wiki-b",
    )
    built: list[_WikiRuntime] = []
    service = RetrievalService(
        knowledge_index_retrieval_service=(_FakeKnowledgeIndexRetrievalService()),
        memory_entry_repository=_FakeMemoryEntryRepo(),
        vector_runtime_resolver_factory=factory,
    )

    def _build(**kwargs):
        runtime = _WikiRuntime(kwargs["runtime_resolver"].scope.workspace_id)
        built.append(runtime)
        return runtime

    monkeypatch.setattr(
        "agent.services.retrieval_service.build_wiki_retrieval_index_service",
        _build,
    )

    first_a = service._get_wiki_vector_retrieval(vector_runtime_scope=scope_a)
    first_b = service._get_wiki_vector_retrieval(vector_runtime_scope=scope_b)
    factory.revisions["workspace-a"] = 2
    second_a = service._get_wiki_vector_retrieval(vector_runtime_scope=scope_a)

    assert first_a.workspace_id == "workspace-a"
    assert first_b.workspace_id == "workspace-b"
    assert second_a.workspace_id == "workspace-a"
    assert first_a.close_calls == 1
    assert first_b.close_calls == 0
    assert second_a.close_calls == 0


def test_wiki_rollout_swap_defers_close_until_search_releases_lease(
    monkeypatch,
):
    entered = Event()
    release = Event()

    class _WikiRuntime:
        def __init__(self, *, blocking: bool) -> None:
            self.blocking = blocking
            self.close_calls = 0

        def hybrid_search(self, query, *, top_k):
            del query, top_k
            if self.blocking:
                entered.set()
                assert release.wait(timeout=5)
            return []

        def close(self) -> None:
            self.close_calls += 1

    factory = _ScopedRuntimeResolverFactory()
    scope = RetrievalVectorRuntimeScope(
        workspace_id="workspace-a",
        wiki_source_id="wiki-a",
    )
    built: list[_WikiRuntime] = []
    service = RetrievalService(
        knowledge_index_retrieval_service=(_FakeKnowledgeIndexRetrievalService()),
        memory_entry_repository=_FakeMemoryEntryRepo(),
        vector_runtime_resolver_factory=factory,
    )

    def _build(**_kwargs):
        runtime = _WikiRuntime(blocking=not built)
        built.append(runtime)
        return runtime

    monkeypatch.setattr(
        "agent.services.retrieval_service.build_wiki_retrieval_index_service",
        _build,
    )

    with ThreadPoolExecutor(max_workers=1) as executor:
        search = executor.submit(
            service._source_adapters["wiki"].search,
            "query",
            top_k=1,
            vector_runtime_scope=scope,
        )
        assert entered.wait(timeout=5)
        factory.revisions["workspace-a"] = 2
        replacement = service._get_wiki_vector_retrieval(
            vector_runtime_scope=scope,
        )

        assert replacement is built[1]
        assert built[0].close_calls == 0
        release.set()
        search.result(timeout=5)

    assert built[0].close_calls == 1
    assert built[1].close_calls == 0


def test_hub_runtime_factory_resolves_each_request_workspace(
    tmp_path: Path,
):
    class _Rollout:
        def __init__(self) -> None:
            self.calls: list[dict[str, str]] = []

        def resolve(self, **kwargs):
            self.calls.append(kwargs)
            return SimpleNamespace(
                config={"provider": "json"},
                config_hash=(f"{kwargs['domain']}:{kwargs['workspace_id']}"),
            )

    rollout = _Rollout()
    factory = HubRetrievalVectorRuntimeResolverFactory(
        rollout_service=rollout,
    )
    scope_a = RetrievalVectorRuntimeScope(
        workspace_id="workspace-a",
        codecompass_repository_id="repo-a",
        wiki_source_id="wiki-a",
    )
    scope_b = RetrievalVectorRuntimeScope(
        workspace_id="workspace-b",
        codecompass_repository_id="repo-b",
        wiki_source_id="wiki-b",
    )

    code_a = factory.codecompass_resolver(scope_a)
    code_b = factory.codecompass_resolver(scope_b)
    assert code_a is not None
    assert code_b is not None

    runtime_a = code_a.resolve(repo_root=tmp_path.resolve())
    runtime_b = code_b.resolve(repo_root=tmp_path.resolve())

    assert runtime_a.trusted_scope == VectorScope(
        "workspace-a",
        "repo-a",
        "default",
        "codecompass",
    )
    assert runtime_b.trusted_scope == VectorScope(
        "workspace-b",
        "repo-b",
        "default",
        "codecompass",
    )
    assert [call["workspace_id"] for call in rollout.calls] == [
        "workspace-a",
        "workspace-b",
    ]


def test_legacy_static_resolver_is_rejected_for_explicit_request_scope(
    monkeypatch,
):
    class _LegacyResolver:
        def cache_signature(self, **_kwargs):
            return ("legacy-workspace-a",)

    captured: list[object] = []
    service = RetrievalService(
        knowledge_index_retrieval_service=(_FakeKnowledgeIndexRetrievalService()),
        memory_entry_repository=_FakeMemoryEntryRepo(),
        codecompass_vector_runtime_resolver=_LegacyResolver(),
    )

    def _build(runtime_resolver):
        captured.append(runtime_resolver)
        return _CloseSpyOrchestrator()

    monkeypatch.setattr(service, "_build_orchestrator", _build)
    service.get_orchestrator(
        vector_runtime_scope=RetrievalVectorRuntimeScope(
            workspace_id="workspace-b",
            codecompass_repository_id="repo-b",
        )
    )

    assert captured
    try:
        captured[0].resolve()
    except ValueError as exc:
        assert str(exc) == ("vector_runtime_request_scope_requires_factory")
    else:
        raise AssertionError("legacy static resolver crossed a request scope")
