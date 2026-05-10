from __future__ import annotations

from agent.services.rag_helper_index_service import RagHelperIndexService


def test_wiki_profiles_are_listed_in_index_profiles():
    service = RagHelperIndexService()
    names = {item["name"] for item in service.list_profiles()}
    assert "wiki-rag-profile-small" in names
    assert "wiki-rag-profile-full" in names
