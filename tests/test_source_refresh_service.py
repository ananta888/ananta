from __future__ import annotations

from pathlib import Path

from agent.sources.source_refresh_service import SourceRefreshService
from agent.sources.source_registry import SourceRegistry
from agent.sources.source_snapshot_store import SourceSnapshotStore


def _keycloak_descriptor(source_id: str = "keycloak-official-docs", *, enabled: bool = True, refresh_interval: str = "24h") -> dict:
    return {
        "schema": "source_descriptor.v1",
        "source_id": source_id,
        "source_type": "keycloak_docs",
        "display_name": "Keycloak",
        "enabled": enabled,
        "trust_level": "official_vendor_project",
        "fetch_source": {
            "url": "https://www.keycloak.org/documentation",
            "method": "GET",
            "refresh_interval": refresh_interval,
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
        "extensions": {},
    }


class _Fetcher:
    def __init__(self, snapshots: SourceSnapshotStore) -> None:
        self.snapshots = snapshots

    def fetch(self, *, descriptor: dict, dry_run: bool = False) -> dict:
        snap = self.snapshots.build_snapshot(
            source_id=str(descriptor["source_id"]),
            descriptor_hash="a" * 64,
            content_payload=[{"url": descriptor["fetch_source"]["url"], "raw_html": "<h1>hello</h1>", "extracted_text": "hello"}],
            metadata_payload={"mock": True},
            status="indexed" if not dry_run else "validating",
            retrieved_at="2026-05-26T00:00:00Z",
        )
        if not dry_run:
            self.snapshots.save_snapshot(snap)
        return {"source_id": descriptor["source_id"], "snapshot": snap, "pages": [{"raw_html": "hello", "extracted_text": "hello"}]}


def test_refresh_service_due_not_due_disabled_and_dry_run(tmp_path: Path) -> None:
    registry = SourceRegistry(root=tmp_path)
    snapshots = SourceSnapshotStore(root=tmp_path)
    registry.create_source(_keycloak_descriptor("src-enabled", enabled=True, refresh_interval="1h"))
    registry.create_source(_keycloak_descriptor("src-disabled", enabled=False))
    service = SourceRefreshService(registry=registry, snapshots=snapshots, keycloak_fetcher=_Fetcher(snapshots))

    plans = service.plan_due_sources()
    by_id = {item["source_id"]: item for item in plans}
    assert by_id["src-enabled"]["action"] == "refresh"
    assert by_id["src-disabled"]["reason_code"] == "source_disabled"

    out = service.refresh_source(source_id="src-enabled", dry_run=True)
    assert out["status"] == "planned"


def test_refresh_service_success_marks_snapshot_and_respects_not_due(tmp_path: Path) -> None:
    registry = SourceRegistry(root=tmp_path)
    snapshots = SourceSnapshotStore(root=tmp_path)
    registry.create_source(_keycloak_descriptor("src-enabled", enabled=True, refresh_interval="999d"))
    service = SourceRefreshService(registry=registry, snapshots=snapshots, keycloak_fetcher=_Fetcher(snapshots))
    ok = service.refresh_source(source_id="src-enabled", dry_run=False)
    assert ok["status"] == "ok"
    latest = snapshots.latest_indexed_snapshot(source_id="src-enabled")
    assert latest is not None
    # because refresh_interval is huge and snapshot was just written
    assert service.is_due(registry.get_source("src-enabled") or {}) is False


def test_refresh_service_refresh_due_sources_returns_skips_and_success(tmp_path: Path) -> None:
    registry = SourceRegistry(root=tmp_path)
    snapshots = SourceSnapshotStore(root=tmp_path)
    registry.create_source(_keycloak_descriptor("src-one", enabled=True))
    registry.create_source(_keycloak_descriptor("src-two", enabled=False))
    service = SourceRefreshService(registry=registry, snapshots=snapshots, keycloak_fetcher=_Fetcher(snapshots))
    result = service.refresh_due_sources(dry_run=False)
    assert any(item["source_id"] == "src-one" and item["status"] == "ok" for item in result)
    assert any(item["source_id"] == "src-two" and item["status"] == "skipped" for item in result)


def test_refresh_service_unknown_source_and_unsupported_type(tmp_path: Path) -> None:
    registry = SourceRegistry(root=tmp_path)
    snapshots = SourceSnapshotStore(root=tmp_path)
    registry.create_source(
        {
            **_keycloak_descriptor("src-web"),
            "source_type": "web_doc",
        }
    )
    service = SourceRefreshService(registry=registry, snapshots=snapshots, keycloak_fetcher=_Fetcher(snapshots))
    unsupported = service.refresh_source(source_id="src-web", dry_run=False)
    assert unsupported["status"] == "failed"
    assert unsupported["reason_code"] == "unsupported_source_type"
    try:
        service.refresh_source(source_id="missing", dry_run=False)
    except ValueError as exc:
        assert "source_not_found" in str(exc)
    else:
        raise AssertionError("expected missing source error")
