from worker.retrieval.wiki_document_normalizer import normalize_wiki_records_for_retrieval


def test_normalize_wiki_records_for_retrieval_keeps_wiki_metadata():
    docs = normalize_wiki_records_for_retrieval(
        records=[
            {
                "chunk_id": "x",
                "content": "Text",
                "article_title": "Artikel",
                "section_title": "Intro",
                "chunk_ordinal": 0,
            }
        ],
        source_id="wiki",
        source_format="xml",
    )
    assert len(docs) == 1
    assert docs[0]["article_title"] == "Artikel"
    assert docs[0]["metadata"]["chunk_ordinal"] == 0
