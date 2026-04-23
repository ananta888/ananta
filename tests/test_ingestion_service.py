from pathlib import Path

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
