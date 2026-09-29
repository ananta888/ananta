from __future__ import annotations

import json
from pathlib import Path

from agent.sources.source_registry import (
    SourceRegistry,
    validate_source_descriptor_payload,
    validate_source_pack_payload,
)
from agent.sources.source_snapshot_store import SourceSnapshotStore, validate_source_snapshot_payload


def _descriptor(source_id: str = "keycloak-official-docs") -> dict:
    return {
        "schema": "source_descriptor.v1",
        "source_id": source_id,
        "source_type": "keycloak_docs",
        "display_name": "Keycloak",
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


def test_source_descriptor_schema_accepts_valid_examples() -> None:
    keycloak = json.loads(Path("sources/keycloak/source_descriptor.json").read_text(encoding="utf-8"))
    wikipedia = json.loads(Path("sources/wikipedia/source_descriptor.json").read_text(encoding="utf-8"))
    assert validate_source_descriptor_payload(keycloak) == []
    assert validate_source_descriptor_payload(wikipedia) == []


def test_source_descriptor_rejects_missing_citation_source() -> None:
    payload = _descriptor()
    payload.pop("citation_source", None)
    assert validate_source_descriptor_payload(payload)


def test_registry_create_update_disable_list_and_duplicate_prevention(tmp_path: Path) -> None:
    registry = SourceRegistry(root=tmp_path)
    created = registry.create_source(_descriptor("src-one"))
    assert created["source_id"] == "src-one"
    assert len(registry.list_sources()) == 1

    created["display_name"] = "Updated"
    updated = registry.update_source(source_id="src-one", descriptor=created)
    assert updated["display_name"] == "Updated"

    disabled = registry.disable_source("src-one")
    assert disabled["enabled"] is False
    assert registry.list_sources(include_disabled=False) == []

    try:
        registry.create_source(_descriptor("src-one"))
    except ValueError as exc:
        assert "already_exists" in str(exc)
    else:
        raise AssertionError("expected duplicate source_id to fail")


def test_snapshot_store_save_latest_and_immutable_behavior(tmp_path: Path) -> None:
    store = SourceSnapshotStore(root=tmp_path)
    snap = store.build_snapshot(
        source_id="src-test",
        descriptor_hash="a" * 64,
        content_payload=[{"k": "v"}],
        metadata_payload={"m": 1},
        status="indexed",
        retrieved_at="2026-05-26T00:00:00Z",
    )
    assert validate_source_snapshot_payload(snap) == []
    store.save_snapshot(snap)
    latest = store.latest_indexed_snapshot(source_id="src-test")
    assert latest is not None
    assert latest["snapshot_id"] == snap["snapshot_id"]
    try:
        store.save_snapshot(snap)
    except ValueError as exc:
        assert "immutable" in str(exc)
    else:
        raise AssertionError("expected immutable snapshot write to fail")


def test_source_pack_schema_accepts_default_example() -> None:
    payload = json.loads(Path("sources/source-packs/ananta-dev-default.source-pack.json").read_text(encoding="utf-8"))
    assert validate_source_pack_payload(payload) == []


def test_source_pack_schema_rejects_missing_sources() -> None:
    payload = json.loads(Path("sources/source-packs/ananta-dev-default.source-pack.json").read_text(encoding="utf-8"))
    payload.pop("sources", None)
    assert validate_source_pack_payload(payload)


def test_registry_lists_and_registers_source_pack(tmp_path: Path) -> None:
    registry = SourceRegistry(root=tmp_path)
    listed = registry.list_source_packs()
    assert any(str(item.get("source_pack_id") or "") == "ananta-dev-default" for item in listed)

    result = registry.register_source_pack(source_pack_id="ananta-dev-default")
    assert result["count"] >= 5
    sources = registry.list_sources()
    assert any(str(item.get("source_id") or "") == "eclipse-platform-official-source" for item in sources)
    eclipse = next(item for item in sources if str(item.get("source_id") or "") == "eclipse-platform-official-source")
    assert str(dict(eclipse.get("extensions") or {}).get("source_pack_id") or "") == "ananta-dev-default"
    assert all(str(item.get("source_id") or "") != "eclipse-swt-official-source" for item in sources)


def test_registry_can_include_optional_sources_explicitly(tmp_path: Path) -> None:
    registry = SourceRegistry(root=tmp_path)
    result = registry.register_source_pack_with_options(source_pack_id="ananta-dev-default", include_optional=True)
    assert result["count"] >= 7
    sources = registry.list_sources()
    assert any(str(item.get("source_id") or "") == "eclipse-swt-official-source" for item in sources)
    optional_row = next(item for item in sources if str(item.get("source_id") or "") == "eclipse-swt-official-source")
    assert bool(optional_row.get("enabled")) is False


def test_registry_rejects_duplicate_source_ids_inside_pack(tmp_path: Path) -> None:
    registry = SourceRegistry(root=tmp_path)
    pack = json.loads(Path("sources/source-packs/ananta-dev-default.source-pack.json").read_text(encoding="utf-8"))
    first = dict(pack["sources"][0])
    duplicate = dict(pack["sources"][1])
    duplicate["source_id"] = str(first["source_id"])
    pack["source_pack_id"] = "duplicate-pack"
    pack["sources"] = [first, duplicate]
    registry.create_source_pack(pack)
    try:
        registry.register_source_pack(source_pack_id="duplicate-pack")
    except ValueError as exc:
        assert "duplicate_source_id_in_pack" in str(exc)
    else:
        raise AssertionError("expected duplicate source ids in pack to fail")


def test_registry_ranks_sources_with_technical_priority(tmp_path: Path) -> None:
    registry = SourceRegistry(root=tmp_path)
    ids = [
        "wikimedia-wikipedia-initial-dump",
        "keycloak-official-docs",
        "eclipse-platform-official-source",
    ]
    ranked_keycloak = registry.rank_sources_for_query(
        source_pack_id="ananta-dev-default",
        source_ids=ids,
        query="How to configure oidc realm client token mapping in keycloak?",
    )
    assert ranked_keycloak[0] == "keycloak-official-docs"
    assert ranked_keycloak[-1] == "wikimedia-wikipedia-initial-dump"

    ranked_eclipse = registry.rank_sources_for_query(
        source_pack_id="ananta-dev-default",
        source_ids=ids,
        query="How to create eclipse pde plugin and osgi extension point?",
    )
    assert ranked_eclipse[0].startswith("eclipse-")
