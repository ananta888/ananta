from agent.hybrid_orchestrator import ContextChunk
from agent.services.retrieval_service import RetrievalService


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

    def get_relevant_context(self, query: str) -> dict[str, object]:
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

    def get_relevant_context(self, query: str) -> dict[str, object]:
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
            "context_text": "[repository_map] secrets/sk-secret-token-1234567890.txt\napi key sk-secret-token-1234567890",
            "token_estimate": 4,
        }


class _FakeKnowledgeIndexRetrievalService:
    def search(self, query: str, *, top_k: int, task_kind=None, retrieval_intent=None):
        self.last_top_k = top_k
        self.last_task_kind = task_kind
        self.last_retrieval_intent = retrieval_intent
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
    service = RetrievalService(knowledge_index_retrieval_service=knowledge, memory_entry_repository=_FakeMemoryEntryRepo())
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
    service = RetrievalService(knowledge_index_retrieval_service=knowledge, memory_entry_repository=_FakeMemoryEntryRepo())
    service._orchestrator = _FakeOrchestrator()
    service._signature = service._config_signature()

    payload = service.retrieve_context("architecture docs overview")

    assert "query_doc_or_architecture" in payload["strategy"]["knowledge_index_reason"]
    assert knowledge.last_top_k == service._config_signature()[5]


def test_retrieval_service_propagates_task_aware_hints():
    knowledge = _FakeKnowledgeIndexRetrievalService()
    service = RetrievalService(knowledge_index_retrieval_service=knowledge, memory_entry_repository=_FakeMemoryEntryRepo())
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
                    "retrieval_document": "summary: parser refactor\nchanged_files: app/parser.py\ntests: passed_signal=True; failed_signal=False",
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


def test_retrieval_service_redacts_sensitive_debug_fields_in_strategy_and_sources():
    knowledge = _FakeKnowledgeIndexRetrievalService()
    service = RetrievalService(knowledge_index_retrieval_service=knowledge, memory_entry_repository=_FakeMemoryEntryRepo())
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
    service = RetrievalService(knowledge_index_retrieval_service=knowledge, memory_entry_repository=_FakeMemoryEntryRepo())
    service._orchestrator = _FakeOrchestrator()
    service._signature = service._config_signature()

    payload = service.retrieve_context("timeout")
    selection_stages = payload["strategy"]["fusion"]["selection_stages"]

    assert selection_stages[0]["stage"] == "all_candidates"
    assert selection_stages[-1]["stage"] == "final"
    assert selection_stages[-1]["count"] == payload["strategy"]["fusion"]["candidate_counts"]["final"]
    assert selection_stages[-1]["top"][0]["engine"] == payload["chunks"][0]["engine"]
