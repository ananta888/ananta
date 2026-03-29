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


class _FakeKnowledgeIndexRetrievalService:
    def search(self, query: str, *, top_k: int):
        self.last_top_k = top_k
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


def test_retrieval_service_merges_knowledge_index_chunks():
    knowledge = _FakeKnowledgeIndexRetrievalService()
    service = RetrievalService(knowledge_index_retrieval_service=knowledge)
    service._orchestrator = _FakeOrchestrator()
    service._signature = service._config_signature()

    payload = service.retrieve_context("timeout")

    assert payload["strategy"]["repository_map"] == 1
    assert payload["strategy"]["knowledge_index"] == 1
    assert payload["strategy"]["knowledge_index_reason"] == "default_balanced_query"
    assert [chunk["engine"] for chunk in payload["chunks"]] == ["knowledge_index", "repository_map"]
    assert "knowledge timeout context" in payload["context_text"]
    assert knowledge.last_top_k >= 1


def test_retrieval_service_prefers_more_knowledge_context_for_doc_queries():
    knowledge = _FakeKnowledgeIndexRetrievalService()
    service = RetrievalService(knowledge_index_retrieval_service=knowledge)
    service._orchestrator = _FakeOrchestrator()
    service._signature = service._config_signature()

    payload = service.retrieve_context("architecture docs overview")

    assert payload["strategy"]["knowledge_index_reason"] == "doc_or_architecture_query"
    assert knowledge.last_top_k == service._config_signature()[5]
