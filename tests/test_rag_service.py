from unittest.mock import MagicMock

from agent.services.rag_service import RagService


def test_rag_service_returns_context_bundle_without_context_text_when_requested():
    retrieval = MagicMock()
    retrieval.retrieve_context.return_value = {
        "query": "find docs",
        "strategy": {"repository_map": 1},
        "policy_version": "v1",
        "chunks": [{"engine": "repository_map", "source": "README.md", "content": "ctx", "score": 1.0, "metadata": {}}],
        "context_text": "ctx",
        "token_estimate": 7,
    }
    service = RagService(retrieval_service=retrieval)

    bundle = service.retrieve_context_bundle("find docs", include_context_text=False)

    assert bundle["bundle_type"] == "retrieval_context"
    assert bundle["query"] == "find docs"
    assert bundle["chunks"]
    assert "context_text" not in bundle


def test_rag_service_builds_grounded_prompt_from_retrieved_context():
    retrieval = MagicMock()
    retrieval.retrieve_context.return_value = {
        "query": "where timeout bug",
        "strategy": {"semantic_search": 1},
        "policy_version": "v1",
        "chunks": [{"engine": "semantic_search", "source": "docs/bug.md", "content": "timeout in worker", "score": 1.2, "metadata": {}}],
        "context_text": "selected context",
        "token_estimate": 12,
    }
    service = RagService(retrieval_service=retrieval)

    bundle, grounded_prompt = service.build_execution_context("where timeout bug")

    assert bundle["context_text"] == "selected context"
    assert "Frage:\nwhere timeout bug" in grounded_prompt
    assert "Kontext:\nselected context" in grounded_prompt
