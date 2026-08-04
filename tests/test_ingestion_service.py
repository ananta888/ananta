import bz2
import gzip
from io import BytesIO
from pathlib import Path

import pytest

from agent.services.artifact_visibility_policy import (
    is_artifact_visible_on_generic_surfaces,
)
from agent.services.ingestion_service import IngestionService


def test_system_artifact_is_hidden_from_its_first_persisted_write(
    monkeypatch,
) -> None:
    persisted = []

    class ArtifactRepository:
        def save(self, artifact):
            persisted.append(artifact)
            return artifact

    checkpoint_calls = 0

    def crash_after_initial_write() -> None:
        nonlocal checkpoint_calls
        checkpoint_calls += 1
        if checkpoint_calls == 2:
            raise RuntimeError("simulated_crash_after_artifact_write")

    monkeypatch.setattr(
        "agent.services.ingestion_service.artifact_repo",
        ArtifactRepository(),
    )
    service = IngestionService(
        artifact_store=object(),
        extraction_service=object(),
    )

    with pytest.raises(
        RuntimeError,
        match="simulated_crash_after_artifact_write",
    ):
        service.upload_artifact(
            filename="worker-output.json",
            content=b"{}",
            created_by="knowledge-index-worker",
            media_type="application/json",
            artifact_metadata={
                "system_artifact_kind": (
                    "knowledge_index_worker_output"
                ),
                "knowledge_index_job_id": "knowledge-index-example",
            },
            execution_checkpoint=crash_after_initial_write,
        )

    assert len(persisted) == 1
    assert persisted[0].artifact_metadata == {
        "ingestion_mode": "raw_artifact_store",
        "system_artifact_kind": "knowledge_index_worker_output",
        "knowledge_index_job_id": "knowledge-index-example",
    }
    assert not is_artifact_visible_on_generic_surfaces(persisted[0])


def test_normal_upload_keeps_legacy_artifact_store_port_compatible(
    monkeypatch,
) -> None:
    class Repository:
        def save(self, value):
            return value

    class LegacyStore:
        def store_bytes(
            self,
            *,
            artifact_id,
            version_number,
            filename,
            content,
            media_type,
        ):
            return {
                "storage_path": f"/tmp/{artifact_id}/{filename}",
                "sha256": "a" * 64,
                "size_bytes": len(content),
                "media_type": media_type,
                "filename": filename,
            }

    monkeypatch.setattr(
        "agent.services.ingestion_service.artifact_repo",
        Repository(),
    )
    monkeypatch.setattr(
        "agent.services.ingestion_service.artifact_version_repo",
        Repository(),
    )

    artifact, version, collection = IngestionService(
        artifact_store=LegacyStore(),
        extraction_service=object(),
    ).upload_artifact(
        filename="result.json",
        content=b"{}",
        created_by="worker",
        media_type="application/json",
    )

    assert artifact.latest_version_id == version.id
    assert collection is None


def test_upload_rejects_non_mapping_initial_metadata_before_persistence(
    monkeypatch,
) -> None:
    persisted = []

    class Repository:
        def save(self, value):
            persisted.append(value)
            return value

    monkeypatch.setattr(
        "agent.services.ingestion_service.artifact_repo",
        Repository(),
    )

    with pytest.raises(ValueError, match="artifact_metadata_invalid"):
        IngestionService(
            artifact_store=object(),
            extraction_service=object(),
        ).upload_artifact(
            filename="result.json",
            content=b"{}",
            created_by="worker",
            artifact_metadata=["not", "a", "mapping"],
        )

    assert persisted == []


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


def test_ingestion_service_import_wiki_corpus_dispatches_by_extension(tmp_path):
    corpus = tmp_path / "simplewiki.xml"
    corpus.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<mediawiki>
  <page>
    <title>Dispatch test</title>
    <revision><text>Dispatch path for XML corpus.</text></revision>
  </page>
</mediawiki>
""",
        encoding="utf-8",
    )
    report = IngestionService().import_wiki_corpus(corpus_path=str(corpus), source_id="dispatch-xml")
    assert report["format"] == "xml"
    assert report["source_id"] == "dispatch-xml"
    assert report["stats"]["normalized_records"] >= 1


def test_ingestion_service_download_report_contains_resume_metadata(monkeypatch, tmp_path):
    payload = b'{"article_title":"A","section_title":"B","content":"hello"}\n'

    class FakeResponse:
        def __init__(self, data: bytes):
            self._buffer = BytesIO(data)
            self.status = 200
            self.headers = {
                "Content-Length": str(len(data)),
                "Accept-Ranges": "bytes",
                "ETag": "abc",
                "Last-Modified": "Mon, 01 Jan 2026 00:00:00 GMT",
            }

        def read(self, size: int = -1) -> bytes:
            return self._buffer.read(size)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr("agent.services.ingestion_service.settings.data_dir", str(tmp_path), raising=False)
    monkeypatch.setattr(
        "agent.services.ingestion_service.urllib.request.urlopen",
        lambda *_args, **_kwargs: FakeResponse(payload),
    )

    report = IngestionService().import_wiki_jsonl_from_url(
        corpus_url="https://example.org/wiki-sample.jsonl",
        source_id="wiki-jsonl-download",
    )

    download = report["download"]
    assert download["bytes"] == len(payload)
    assert download["status_code"] == 200
    assert download["requested_range"] is False
    assert download["headers"]["accept_ranges"] == "bytes"
