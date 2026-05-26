from __future__ import annotations

from agent.sources.keycloak_ingest import ingest_keycloak_pages


def test_keycloak_ingest_creates_chunk_with_source_reference() -> None:
    pages = [
        {
            "url": "https://www.keycloak.org/guides/server/admin",
            "raw_html": "<html><body>" + ("keycloak admin guide text " * 600) + "</body></html>",
        }
    ]
    out = ingest_keycloak_pages(
        source_id="keycloak-official-docs",
        snapshot_id="snap_1234567890ab",
        citation_source={"license_ref": "license_unknown", "retrieved_at": "2026-05-26T00:00:00Z", "citation_text": "Keycloak citation"},
        pages=pages,
    )
    assert out
    first = out[0]
    assert first["doc_title"]
    assert first["source_reference"]["source_id"] == "keycloak-official-docs"
    assert first["source_reference"]["snapshot_id"] == "snap_1234567890ab"
    assert first["source_reference"]["canonical_url"].startswith("https://")

