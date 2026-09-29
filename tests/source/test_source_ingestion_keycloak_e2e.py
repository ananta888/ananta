from __future__ import annotations

from pathlib import Path

from agent.sources.citation_formatter import format_citation
from agent.sources.keycloak_fetcher import KeycloakDocsFetcher
from agent.sources.keycloak_ingest import ingest_keycloak_pages
from agent.sources.source_snapshot_store import SourceSnapshotStore


def test_keycloak_mini_import_e2e_offline(tmp_path: Path) -> None:
    fixture = Path("tests/fixtures/sources/keycloak/mini.html").resolve()
    descriptor = {
        "source_id": "keycloak-official-docs",
        "source_type": "keycloak_docs",
        "display_name": "Keycloak",
        "fetch_source": {"url": fixture.as_uri()},
        "citation_source": {
            "title": "Keycloak Mini Docs",
            "publisher": "keycloak.org",
            "canonical_url": fixture.as_uri(),
            "retrieved_at": "2026-05-26T00:00:00Z",
            "license_ref": "license_unknown",
            "version_label": "mini",
        },
        "extensions": {"descriptor_hash": "a" * 64},
    }
    snapshots = SourceSnapshotStore(root=tmp_path)
    fetched = KeycloakDocsFetcher(snapshot_store=snapshots).fetch(descriptor=descriptor, dry_run=False)
    snapshot = fetched["snapshot"]
    assert snapshot["status"] == "indexed"
    chunks = ingest_keycloak_pages(
        source_id=descriptor["source_id"],
        snapshot_id=snapshot["snapshot_id"],
        citation_source=descriptor["citation_source"],
        pages=fetched["pages"],
        chunk_words_target=100,
    )
    assert chunks
    assert chunks[0]["source_reference"]["snapshot_id"] == snapshot["snapshot_id"]
    citation = format_citation(descriptor=descriptor, snapshot=snapshot, output_format="long")
    assert "snapshot_id=" in citation["long"]
    # stable hash for same imported content
    again = KeycloakDocsFetcher(snapshot_store=SourceSnapshotStore(root=tmp_path)).fetch(descriptor=descriptor, dry_run=False)
    assert again["snapshot"]["content_hash"] == snapshot["content_hash"]

