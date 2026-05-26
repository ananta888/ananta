from __future__ import annotations

from pathlib import Path

from agent.sources.source_pack_service import SourcePackService
from agent.sources.source_registry import SourceRegistry
from agent.sources.source_snapshot_store import SourceSnapshotStore


def _service(tmp_path: Path) -> SourcePackService:
    return SourcePackService(
        registry=SourceRegistry(root=tmp_path),
        snapshots=SourceSnapshotStore(root=tmp_path),
    )


def test_source_pack_bootstrap_dry_run_includes_warning(tmp_path: Path) -> None:
    service = _service(tmp_path)
    result = service.bootstrap(source_pack_id="ananta-dev-default", dry_run=True)
    assert result["status"] == "planned"
    assert "wikipedia_dump_large_download_warning" in list(result.get("warnings") or [])
    assert any(str(item.get("source_id") or "") == "wikimedia-wikipedia-initial-dump" for item in list(result.get("selected_sources") or []))


def test_source_pack_bootstrap_generates_snapshots_and_bundle(tmp_path: Path) -> None:
    service = _service(tmp_path)
    result = service.bootstrap(
        source_pack_id="ananta-dev-default",
        dry_run=False,
        skip_source_ids=["wikimedia-wikipedia-initial-dump"],
    )
    assert result["status"] == "ok"
    assert "wikimedia-wikipedia-initial-dump" in list(result.get("skip_source_ids") or [])
    assert all(
        str(item.get("source_id") or "") != "wikimedia-wikipedia-initial-dump"
        for item in list(result.get("selected_sources") or [])
    )

    bundle = dict(result.get("codecompass_bundle") or {})
    assert bundle.get("schema") == "codecompass_bundle.v1"
    assert bundle.get("metadata_only") is True
    assert "index_hash" in bundle
    assert "plugin_xml_extensions" in list(dict(bundle.get("index_profile") or {}).get("extractors") or [])
    assert Path(str(bundle.get("bundle_path") or "")).exists()


def test_source_pack_retrieval_rules_scope_sources(tmp_path: Path) -> None:
    service = _service(tmp_path)
    eclipse_scope = service.resolve_retrieval_sources(
        source_pack_id="ananta-dev-default",
        query="How to define extension points in eclipse plugin.xml with OSGi MANIFEST?",
    )
    assert eclipse_scope
    assert all(item.startswith("eclipse-") for item in eclipse_scope)

    keycloak_scope = service.resolve_retrieval_sources(
        source_pack_id="ananta-dev-default",
        query="How to map realm roles to token claims in keycloak?",
    )
    assert keycloak_scope == ["keycloak-official-docs"]


def test_source_pack_bootstrap_license_policy_can_block(tmp_path: Path) -> None:
    service = _service(tmp_path)
    registry = service.registry
    pack = registry.get_source_pack("ananta-dev-default")
    assert isinstance(pack, dict)
    modified = dict(pack)
    modified["source_pack_id"] = "blocked-license-pack"
    modified["license_policy"] = {
        "require_license_ref": True,
        "block_on_missing_license": True,
        "allowed_licenses": ["EPL-2.0", "CC BY-SA 4.0"],
    }
    registry.create_source_pack(modified)
    result = service.bootstrap(source_pack_id="blocked-license-pack")
    assert result["status"] == "failed"
    assert result["reason_code"] == "license_policy_blocked"
    assert any("license_not_allowed:keycloak-official-docs:license_unknown" in item for item in list(result["license_policy_report"]["blocking_errors"]))


def test_source_pack_doctor_reports_ready_and_missing_cases(tmp_path: Path) -> None:
    service = _service(tmp_path)
    report_initial = service.doctor(source_pack_id="ananta-dev-default")
    assert report_initial["ready"] is False
    assert any(
        item in {"register_source:keycloak-official-docs", "refresh_or_bootstrap:keycloak-official-docs"}
        for item in list(report_initial.get("next_steps") or [])
    )

    boot = service.bootstrap(source_pack_id="ananta-dev-default")
    assert boot["status"] == "ok"
    report_ready = service.doctor(source_pack_id="ananta-dev-default")
    assert report_ready["ready"] is True
    assert report_ready["bundle_ready"] is True


def test_source_pack_doctor_detects_index_failed(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.registry.register_source_pack(source_pack_id="ananta-dev-default", overwrite_existing=True)
    snapshots = service.snapshots
    failed = snapshots.build_snapshot(
        source_id="keycloak-official-docs",
        descriptor_hash="a" * 64,
        content_payload=[{"k": "v"}],
        metadata_payload={},
        status="failed",
        reason_code="index_failed",
        human_message="failed fixture",
    )
    snapshots.save_snapshot(failed)
    report = service.doctor(source_pack_id="ananta-dev-default")
    assert report["ready"] is False
    assert "repair_index:keycloak-official-docs" in list(report.get("next_steps") or [])
