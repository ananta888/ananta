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
    assert bundle["chunk_count"] == 1
    assert bundle["explainability"]["engines"] == ["repository_map"]
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


def test_rag_service_exposes_knowledge_index_explainability():
    retrieval = MagicMock()
    retrieval.retrieve_context.return_value = {
        "query": "payment timeout",
        "strategy": {"knowledge_index": 1},
        "policy_version": "v1",
        "chunks": [
            {
                "engine": "knowledge_index",
                "source": "docs/payment-timeouts.md",
                "content": "timeout handling",
                "score": 2.0,
                "metadata": {
                    "artifact_id": "artifact-1",
                    "knowledge_index_id": "idx-1",
                    "record_kind": "md_section",
                    "collection_ids": ["collection-1"],
                    "collection_names": ["payments-docs"],
                },
            }
        ],
        "context_text": "timeout handling",
        "token_estimate": 5,
    }
    service = RagService(retrieval_service=retrieval)

    bundle = service.retrieve_context_bundle("payment timeout")

    assert bundle["explainability"]["collection_names"] == ["payments-docs"]
    assert bundle["explainability"]["artifact_ids"] == ["artifact-1"]
    assert bundle["explainability"]["chunk_types"] == ["md_section"]


def test_rag_service_compact_policy_trims_chunks_and_hides_context_text():
    retrieval = MagicMock()
    retrieval.retrieve_context.return_value = {
        "query": "find docs",
        "strategy": {"repository_map": 1},
        "policy_version": "v1",
        "chunks": [
            {"engine": "repository_map", "source": f"doc-{idx}.md", "content": "ctx", "score": 1.0, "metadata": {}}
            for idx in range(5)
        ],
        "context_text": "ctx",
        "token_estimate": 21,
    }
    service = RagService(retrieval_service=retrieval)

    bundle = service.retrieve_context_bundle("find docs", include_context_text=False, max_chunks=2, policy_mode="compact")

    assert "context_text" not in bundle
    assert bundle["chunk_count"] == 2
    assert len(bundle["chunks"]) == 2
    assert bundle["context_policy"] == {
        "mode": "compact",
        "include_context_text": False,
        "max_chunks": 2,
    }


def test_rag_service_standard_policy_records_effective_context_policy():
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

    bundle = service.retrieve_context_bundle("find docs", include_context_text=True, max_chunks=4, policy_mode="standard")

    assert bundle["context_text"] == "ctx"
    assert bundle["context_policy"] == {
        "mode": "standard",
        "include_context_text": True,
        "max_chunks": 4,
    }
