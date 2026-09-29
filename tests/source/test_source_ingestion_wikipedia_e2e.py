from __future__ import annotations

from pathlib import Path

from agent.sources.citation_formatter import format_citation
from agent.sources.wikipedia_ingest import ingest_wikipedia_dump


def test_wikipedia_fixture_dump_e2e_offline() -> None:
    fixture = Path("tests/fixtures/sources/wikipedia/mini.xml").resolve()
    descriptor = {
        "source_id": "wikimedia-wikipedia-initial-dump",
        "source_type": "wikimedia_dump",
        "display_name": "Wikipedia",
        "citation_source": {
            "title": "Wikimedia Dumps",
            "publisher": "Wikimedia Foundation",
            "canonical_url": "https://dumps.wikimedia.org/",
            "retrieved_at": "2026-05-26T00:00:00Z",
            "license_ref": "CC BY-SA 4.0",
            "citation_text": "Wikipedia/Wikimedia attribution",
        },
        "extensions": {"language_default": "de"},
    }
    report = ingest_wikipedia_dump(
        corpus_path=fixture,
        source_id=descriptor["source_id"],
        snapshot_id="snap_abcdef012345",
        citation_source=descriptor["citation_source"],
        max_chunk_chars=220,
    )
    assert report["chunks"]
    first = report["chunks"][0]
    assert first["article_title"]
    assert first["source_reference"]["source_id"] == descriptor["source_id"]
    assert first["license_ref"] == "CC BY-SA 4.0"
    assert any(str(item.get("reason_code") or "") == "redirect" for item in report["issues"])
    citation = format_citation(descriptor=descriptor, snapshot={"snapshot_id": "snap_abcdef012345", "content_hash": "a" * 64}, output_format="long")
    assert "language=de" in citation["long"]

