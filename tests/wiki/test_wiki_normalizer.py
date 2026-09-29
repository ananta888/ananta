from pathlib import Path

from agent.services.wiki_normalizer import WikiRecordNormalizer


def test_wiki_normalizer_filters_redirects_and_namespaces(tmp_path):
    normalizer = WikiRecordNormalizer(allowed_namespaces={0})
    source = Path(tmp_path / "sample.xml")
    source.write_text("<x/>", encoding="utf-8")

    records, issue = normalizer.normalize_item(
        item={"kind": "page", "title": "A", "text": "Body", "namespace": 1, "is_redirect": False},
        source_path=source,
        source_id="wiki",
        ordinal=1,
        default_language="de",
        source_format="xml",
    )
    assert records == []
    assert issue and issue["error"] == "namespace_filtered"

    records2, issue2 = normalizer.normalize_item(
        item={"kind": "page", "title": "A", "text": "#REDIRECT [[B]]", "namespace": 0, "is_redirect": True},
        source_path=source,
        source_id="wiki",
        ordinal=2,
        default_language="de",
        source_format="xml",
    )
    assert records2 == []
    assert issue2 and issue2["error"] == "redirect_filtered"


def test_wiki_normalizer_produces_chunk_records(tmp_path):
    normalizer = WikiRecordNormalizer(allowed_namespaces={0}, max_chunk_chars=100)
    source = Path(tmp_path / "sample.xml")
    source.write_text("<x/>", encoding="utf-8")
    records, issue = normalizer.normalize_item(
        item={"kind": "page", "title": "Artikel", "text": "== A ==\nText."},
        source_path=source,
        source_id="wiki",
        ordinal=1,
        default_language="de",
        source_format="xml",
    )
    assert issue is None
    assert records
    assert records[0]["kind"] == "wiki_section_chunk"
    assert records[0]["article_title"] == "Artikel"

