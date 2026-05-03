from pathlib import Path

import gzip
import bz2
from io import BytesIO

import pytest

from agent.services.ingestion_service import IngestionService


def test_ingestion_service_import_wiki_jsonl_normalizes_records_and_reports_issues(tmp_path):
    corpus = tmp_path / "wiki-mvp.jsonl"
    corpus.write_text(
        "\n".join(
            [
                '{"article_title":"Payment retries","section_title":"Timeout","language":"en","content":"Workers retry after timeout. Backoff applies."}',
                '{"article_title":"Broken"}',
                'not-json',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    report = IngestionService().import_wiki_jsonl(corpus_path=str(corpus), source_id="wiki-mvp", default_language="en")

    assert report["source_scope"] == "wiki"
    assert report["source_id"] == "wiki-mvp"
    assert report["stats"]["input_lines"] == 3
    assert report["stats"]["normalized_records"] >= 1
    assert report["stats"]["issues"] == 2
    first = report["records"][0]
    assert first["kind"] == "wiki_section_chunk"
    assert first["article_title"] == "Payment retries"
    assert first["section_title"] == "Timeout"
    assert first["chunk_id"].startswith("wiki:")


def test_ingestion_service_import_wiki_jsonl_strict_mode_fails_on_invalid_record(tmp_path):
    corpus = tmp_path / "wiki-invalid.jsonl"
    corpus.write_text('{"article_title":"Broken"}\n', encoding="utf-8")

    with pytest.raises(ValueError):
        IngestionService().import_wiki_jsonl(corpus_path=str(corpus), strict=True)


def test_ingestion_service_import_wiki_jsonl_is_deterministic_for_same_input(tmp_path):
    corpus = tmp_path / "wiki-deterministic.jsonl"
    corpus.write_text(
        "\n".join(
            [
                '{"article_title":"A","section_title":"S2","content":"two."}',
                '{"article_title":"A","section_title":"S1","content":"one."}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    service = IngestionService()
    first = service.import_wiki_jsonl(corpus_path=str(corpus), source_id="wiki-deterministic")
    second = service.import_wiki_jsonl(corpus_path=str(corpus), source_id="wiki-deterministic")

    assert [item["chunk_id"] for item in first["records"]] == [item["chunk_id"] for item in second["records"]]
    assert first["records"] == second["records"]
    assert Path(first["corpus_path"]).name == "wiki-deterministic.jsonl"


def test_ingestion_service_import_wiki_xml_mediawiki_extracts_records(tmp_path):
    corpus = tmp_path / "simplewiki.xml"
    corpus.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<mediawiki>
  <page>
    <title>Payment retries</title>
    <revision><text>Workers retry after timeout. Backoff applies.</text></revision>
  </page>
</mediawiki>
""",
        encoding="utf-8",
    )

    report = IngestionService().import_wiki_xml(corpus_path=str(corpus), source_id="wiki-xml")

    assert report["source_scope"] == "wiki"
    assert report["source_id"] == "wiki-xml"
    assert report["format"] == "xml"
    assert report["stats"]["input_pages"] == 1
    assert report["stats"]["normalized_records"] >= 1
    assert report["records"][0]["article_title"] == "Payment retries"
    assert report["records"][0]["import_metadata"]["format"] == "xml"


def test_ingestion_service_import_wiki_xml_from_url_supports_gz_abstract(monkeypatch, tmp_path):
    xml_payload = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed>
  <doc>
    <title>Retrieval-augmented generation</title>
    <abstract>RAG combines retrieval and generation for grounded answers.</abstract>
  </doc>
</feed>
"""
    compressed = gzip.compress(xml_payload)

    class FakeResponse:
        def __init__(self, data: bytes):
            self._buffer = BytesIO(data)
            self.status = 200

        def read(self, size: int = -1) -> bytes:
            return self._buffer.read(size)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr("agent.services.ingestion_service.settings.data_dir", str(tmp_path), raising=False)
    monkeypatch.setattr(
        "agent.services.ingestion_service.urllib.request.urlopen",
        lambda *_args, **_kwargs: FakeResponse(compressed),
    )

    report = IngestionService().import_wiki_jsonl_from_url(
        corpus_url="https://dumps.wikimedia.org/simplewiki/latest/simplewiki-latest-abstract1.xml.gz",
        source_id="wiki-abstract-gz",
    )

    assert report["source_scope"] == "wiki"
    assert report["source_id"] == "wiki-abstract-gz"
    assert report["format"] == "xml"
    assert report["stats"]["input_docs"] == 1
    assert report["stats"]["normalized_records"] >= 1
    assert report["download"]["url"].endswith(".xml.gz")


def test_ingestion_service_import_wiki_multistream_uses_index(tmp_path):
    fragment = b"""
  <page>
    <title>Multistream article</title>
    <revision><text>Indexed block content for German Wikipedia RAG.</text></revision>
  </page>
"""
    corpus = tmp_path / "dewiki-latest-pages-articles-multistream.xml.bz2"
    compressed = bz2.compress(fragment)
    corpus.write_bytes(compressed)
    index = tmp_path / "dewiki-latest-pages-articles-multistream-index.txt"
    index.write_text("0:1:Multistream article\n", encoding="utf-8")

    report = IngestionService().import_wiki_xml(
        corpus_path=str(corpus),
        index_path=str(index),
        source_id="dewiki-multistream",
        default_language="de",
    )

    assert report["source_id"] == "dewiki-multistream"
    assert report["multistream_index"]["enabled"] is True
    assert report["index_path"] == str(index.resolve())
    assert report["jsonl_cache_path"]
    assert Path(str(report["jsonl_cache_path"])).exists()
    assert report["records"][0]["article_title"] == "Multistream article"
