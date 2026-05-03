from worker.retrieval.wiki_document_normalizer import normalize_wiki_document


def test_normalize_wiki_document_keeps_wiki_metadata():
    doc = normalize_wiki_document(
        {
            "id": "x",
            "kind": "wiki_section_chunk",
            "content": "Text",
            "source": "wiki",
            "article_title": "Artikel",
            "section_title": "Intro",
            "chunk_index": 0,
            "chunk_total": 1,
            "language": "de",
            "metadata": {"source_format": "xml"},
        }
    )
    assert doc["title"] == "Artikel"
    assert doc["metadata"]["section_title"] == "Intro"

