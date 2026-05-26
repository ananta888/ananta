from __future__ import annotations

from pathlib import Path

from agent.sources.wikipedia_ingest import ingest_wikipedia_dump


def test_wikipedia_ingest_generates_chunks_with_attribution() -> None:
    corpus = Path("tests/fixtures/sources/wikipedia/mini.xml").resolve()
    report = ingest_wikipedia_dump(
        corpus_path=corpus,
        source_id="wikimedia-wikipedia-initial-dump",
        snapshot_id="snap_1234567890ab",
        citation_source={
            "retrieved_at": "2026-05-26T00:00:00Z",
            "license_ref": "CC BY-SA 4.0",
            "citation_text": "Wikipedia/Wikimedia attribution",
        },
        max_chunk_chars=200,
    )
    assert report["chunk_count"] >= 1
    first = report["chunks"][0]
    assert first["source_reference"]["source_id"] == "wikimedia-wikipedia-initial-dump"
    assert first["source_reference"]["snapshot_id"] == "snap_1234567890ab"
    assert first["license_ref"] == "CC BY-SA 4.0"
    assert "Wikipedia/Wikimedia" in first["attribution_text"]


def test_wikipedia_ingest_marks_disambiguation_and_skips_redirects() -> None:
    corpus = Path("tests/fixtures/sources/wikipedia/mini.xml").resolve()
    report = ingest_wikipedia_dump(
        corpus_path=corpus,
        source_id="wikimedia-wikipedia-initial-dump",
        snapshot_id="snap_1234567890ab",
        citation_source={"retrieved_at": "2026-05-26T00:00:00Z", "license_ref": "CC BY-SA 4.0"},
    )
    reasons = {str(item.get("reason_code") or "") for item in report["issues"]}
    assert "redirect" in reasons
    assert any(bool(chunk["flags"]["is_disambiguation"]) for chunk in report["chunks"])

