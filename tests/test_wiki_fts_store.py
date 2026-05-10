from __future__ import annotations

from worker.retrieval.wiki_fts_store import WikiFtsStore


def test_wiki_fts_store_build_and_search(tmp_path):
    store = WikiFtsStore(db_path=tmp_path / "wiki_fts.sqlite")
    docs = [
        {
            "record_id": "wiki:c1",
            "kind": "wiki_section_chunk",
            "file": "wiki/payment.md",
            "parent_id": "",
            "role_labels": [],
            "importance_score": 1.0,
            "generated_code": False,
            "manifest_hash": "mh-wiki-1",
            "document_hash": "dh-wiki-1",
            "text_fields": {
                "symbol_text": "Ananta",
                "path_text": "wiki/payment.md",
                "kind_text": "wiki_section_chunk",
                "summary_text": "retry policy",
                "content_text": "Ananta wiki retry policy",
                "relation_text": "",
                "focus_text": "retry",
            },
        }
    ]
    store.rebuild(documents=docs, retrieval_cache_state="wiki-cache-1")
    hits = store.search(query="retry policy", top_k=3)
    assert hits
