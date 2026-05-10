from __future__ import annotations

from pathlib import Path

from agent.services.ingestion_service import IngestionService


def test_wiki_fixture_flow_imports_without_network():
    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "wiki" / "simplewiki.xml"
    report = IngestionService().import_wiki_corpus(corpus_path=str(fixture), source_id="wiki-fixture-e2e")
    assert report["source_scope"] == "wiki"
    assert report["source_id"] == "wiki-fixture-e2e"
    assert int((report.get("stats") or {}).get("input_pages") or 0) >= 1
    assert len(list(report.get("records") or [])) >= 1
