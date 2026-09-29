from __future__ import annotations

from agent.services.knowledge_index_retrieval_service import KnowledgeIndexRetrievalService


def test_wiki_retrieval_quality_scope_metadata_is_wiki():
    service = KnowledgeIndexRetrievalService()
    # Deterministic smoke contract: source preflight must expose dedicated wiki scope.
    preflight = service.get_source_preflight()
    assert "wiki" in preflight
    assert "status" in preflight["wiki"]
