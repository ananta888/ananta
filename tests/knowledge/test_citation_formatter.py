from __future__ import annotations

from agent.sources.citation_formatter import format_citation


def test_citation_formatter_keycloak_long_contains_version_and_snapshot() -> None:
    descriptor = {
        "source_id": "keycloak-official-docs",
        "source_type": "keycloak_docs",
        "display_name": "Keycloak docs",
        "citation_source": {
            "title": "Keycloak Documentation",
            "publisher": "keycloak.org",
            "canonical_url": "https://www.keycloak.org/documentation",
            "retrieved_at": "2026-05-26T00:00:00Z",
            "license_ref": "license_unknown",
            "version_label": "26.0",
        },
    }
    snapshot = {"snapshot_id": "snap_1234abcd5678", "content_hash": "a" * 64}
    rendered = format_citation(descriptor=descriptor, snapshot=snapshot, output_format="long")
    assert "version=26.0" in rendered["long"]
    assert "snapshot_id=snap_1234abcd5678" in rendered["long"]


def test_citation_formatter_wikipedia_mentions_language_and_attribution() -> None:
    descriptor = {
        "source_id": "wikimedia-wikipedia-initial-dump",
        "source_type": "wikimedia_dump",
        "display_name": "Wikipedia dump",
        "citation_source": {
            "title": "Wikimedia Dumps",
            "publisher": "Wikimedia Foundation",
            "canonical_url": "https://dumps.wikimedia.org/",
            "retrieved_at": "2026-05-26T00:00:00Z",
            "license_ref": "CC BY-SA 4.0",
            "citation_text": "Wikipedia/Wikimedia attribution text",
        },
        "extensions": {"language_default": "de"},
    }
    rendered = format_citation(descriptor=descriptor, snapshot=None, output_format="markdown")
    assert "language=de" in rendered["long"]
    assert "attribution=Wikipedia/Wikimedia attribution text" in rendered["long"]
    assert "**License:** CC BY-SA 4.0" in rendered["markdown"]


def test_citation_formatter_handles_missing_optional_fields() -> None:
    descriptor = {"source_id": "s1", "source_type": "web_doc", "display_name": "Any", "citation_source": {}}
    rendered = format_citation(descriptor=descriptor, snapshot=None, output_format="short")
    assert rendered["short"]
    assert rendered["rendered"] == rendered["short"]

