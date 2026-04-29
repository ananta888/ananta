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
                    "source_type": "artifact",
                    "source_id": "artifact-1",
                    "chunk_id": "artifact:chunk-1",
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
    assert bundle["explainability"]["source_types"] == ["artifact"]
    assert bundle["explainability"]["source_type_counts"] == {"artifact": 1}


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
    assert bundle["context_policy"]["mode"] == "compact"
    assert bundle["context_policy"]["include_context_text"] is False
    assert bundle["context_policy"]["max_chunks"] == 2
    assert bundle["context_policy"]["total_budget_tokens"] == 12000
    assert bundle["context_policy"]["window_profile"] == "standard_32k"
    assert bundle["context_policy"]["bundle_strategy"] == "minimal"
    assert bundle["context_policy"]["explainability_level"] == "minimal"
    assert bundle["context_policy"]["chunk_text_style"] == "compressed_snippets"
    assert isinstance(bundle["context_policy"].get("source_prioritization_rules"), list)
    assert bundle["why_this_context"]["mode"] == "compact"
    assert isinstance(bundle["selection_trace"], dict)


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
    assert bundle["context_policy"]["mode"] == "standard"
    assert bundle["context_policy"]["include_context_text"] is True
    assert bundle["context_policy"]["max_chunks"] == 4
    assert bundle["context_policy"]["total_budget_tokens"] == 32000
    assert bundle["context_policy"]["window_profile"] == "standard_32k"
    assert bundle["context_policy"]["bundle_strategy"] == "balanced"
    assert bundle["context_policy"]["explainability_level"] == "balanced"
    assert bundle["context_policy"]["chunk_text_style"] == "balanced_snippets"
    assert isinstance(bundle["context_policy"].get("source_prioritization_rules"), list)


def test_rag_service_full_mode_keeps_detailed_explainability_profile():
    retrieval = MagicMock()
    retrieval.retrieve_context.return_value = {
        "query": "find deep architecture context",
        "strategy": {"repository_map": 1},
        "policy_version": "v1",
        "chunks": [
            {"engine": "repository_map", "source": f"file-{idx}.md", "content": "ctx " * 800, "score": 1.0, "metadata": {}}
            for idx in range(10)
        ],
        "context_text": "ctx",
        "token_estimate": 42,
    }
    service = RagService(retrieval_service=retrieval)

    bundle = service.retrieve_context_bundle("find deep architecture context", policy_mode="full")

    assert bundle["context_policy"]["bundle_strategy"] == "deep"
    assert bundle["context_policy"]["explainability_level"] == "detailed"
    assert bundle["context_policy"]["chunk_text_style"] == "detailed_context"
    assert len(bundle["explainability"]["sources"]) <= 10


def test_rag_service_redacts_sensitive_values_in_explainability_and_selection_trace(monkeypatch):
    retrieval = MagicMock()
    retrieval.retrieve_context.return_value = {
        "query": "inspect secrets",
        "strategy": {
            "knowledge_index_reason": "matched sk-secret-token-1234567890",
            "result_memory_reason": "used token=sk-secret-token-1234567890",
            "fusion": {"candidate_counts": {"all": 2, "final": 1}},
        },
        "policy_version": "v1",
        "chunks": [
            {
                "engine": "knowledge_index",
                "source": "secrets/sk-secret-token-1234567890.txt",
                "content": "token should not leak",
                "score": 2.0,
                "metadata": {},
            }
        ],
        "context_text": "token should not leak",
        "token_estimate": 8,
    }
    from agent.config import settings
    monkeypatch.setattr(settings, "rag_redact_sensitive", True)
    service = RagService(retrieval_service=retrieval)

    bundle = service.retrieve_context_bundle("inspect secrets")

    assert "sk-secret-token-1234567890" not in str(bundle.get("explainability") or {})
    assert "sk-secret-token-1234567890" not in str(bundle.get("why_this_context") or {})
    assert "sk-secret-token-1234567890" not in str(bundle.get("selection_trace") or {})


def test_rag_service_forwards_source_type_selection_to_retrieval():
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

    service.retrieve_context_bundle("find docs", source_types=["repo", "artifact"])

    retrieval.retrieve_context.assert_called_once()
    assert retrieval.retrieve_context.call_args.kwargs["source_types"] == ["repo", "artifact"]


def test_rag_service_applies_provenance_visibility_policy():
    retrieval = MagicMock()
    retrieval.retrieve_context.return_value = {
        "query": "payment retries",
        "strategy": {"knowledge_index": 1},
        "policy_version": "v1",
        "chunks": [
            {
                "engine": "knowledge_index",
                "source": "wiki/payment.md",
                "content": "retry guidance",
                "score": 1.3,
                "metadata": {
                    "record_kind": "wiki_section",
                    "source_type": "wiki",
                    "source_id": "Payment retries",
                    "chunk_id": "wiki:chunk-1",
                    "collection_names": ["wiki-mvp"],
                },
            }
        ],
        "context_text": "retry guidance",
        "token_estimate": 5,
    }
    service = RagService(retrieval_service=retrieval)

    standard_bundle = service.retrieve_context_bundle("payment retries", provenance_visibility="standard")
    admin_bundle = service.retrieve_context_bundle("payment retries", provenance_visibility="admin")

    standard_source = (standard_bundle["explainability"]["sources"] or [])[0]
    admin_source = (admin_bundle["explainability"]["sources"] or [])[0]
    assert "source_id" not in standard_source
    assert "chunk_id" not in standard_source
    assert admin_source["source_id"] == "Payment retries"
    assert admin_source["chunk_id"] == "wiki:chunk-1"
    assert standard_bundle["provenance_policy"]["visibility_level"] == "standard"
    assert admin_bundle["provenance_policy"]["visibility_level"] == "admin"
