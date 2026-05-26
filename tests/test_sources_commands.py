from __future__ import annotations

from client_surfaces.operator_tui.commands import execute_command
from client_surfaces.operator_tui.models import OperatorState
from agent.sources.source_registry import SourceRegistry
from agent.sources.source_snapshot_store import SourceSnapshotStore


def _descriptor() -> dict:
    return {
        "schema": "source_descriptor.v1",
        "source_id": "keycloak-official-docs",
        "source_type": "keycloak_docs",
        "display_name": "Keycloak docs",
        "enabled": True,
        "trust_level": "official_vendor_project",
        "fetch_source": {
            "url": "https://www.keycloak.org/documentation",
            "method": "GET",
            "refresh_interval": "24h",
            "cache_policy": "respect_http_cache_headers",
            "expected_format": "html",
        },
        "citation_source": {
            "canonical_url": "https://www.keycloak.org/documentation",
            "title": "Keycloak docs",
            "publisher": "keycloak.org",
            "version_label": "latest",
            "retrieved_at": "2026-05-26T00:00:00Z",
            "license_ref": "license_unknown",
            "citation_text": "Keycloak docs citation",
        },
        "license": {"name": "Unknown", "ref": "license_unknown"},
        "snapshot_policy": {"immutable": True, "dedupe_by_hash": True},
        "retention_policy": {"keep_latest": 10, "max_age_days": 365},
    }


def test_sources_commands_list_refresh_snapshots_cite(monkeypatch, tmp_path) -> None:
    from agent.config import settings

    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    registry = SourceRegistry()
    snapshots = SourceSnapshotStore()
    registry.create_source(_descriptor())
    snap = snapshots.build_snapshot(
        source_id="keycloak-official-docs",
        descriptor_hash="a" * 64,
        content_payload=[{"url": "https://www.keycloak.org/documentation"}],
        metadata_payload={"k": 1},
        status="indexed",
        retrieved_at="2026-05-26T00:00:00Z",
    )
    snapshots.save_snapshot(snap)
    state = OperatorState(endpoint="http://localhost")

    listed = execute_command(":sources list", state)
    assert listed.handled is True
    assert "keycloak-official-docs" in listed.message

    refreshed = execute_command(":sources refresh keycloak-official-docs --dry-run", listed.state)
    assert refreshed.handled is True
    assert "\"status\": \"planned\"" in refreshed.message

    snaps = execute_command(":sources snapshots keycloak-official-docs", refreshed.state)
    assert snaps.handled is True
    assert "snapshot_id" in snaps.message

    cite = execute_command(":sources cite keycloak-official-docs", snaps.state)
    assert cite.handled is True
    assert "snapshot_id=" in cite.message

