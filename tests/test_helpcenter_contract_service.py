from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from agent.services.helpcenter_contract_service import (
    build_report_paths,
    default_helpcenter_index,
    ensure_helpcenter_structure,
    load_helpcenter_index,
    upsert_helpcenter_index_entry,
    validate_helpcenter_analysis,
    validate_helpcenter_index,
    validate_helpcenter_message,
    write_helpcenter_index,
)


def _valid_message() -> dict:
    return {
        "message_id": "msg-1",
        "source_kind": "github_workflow_failure",
        "source_ref": "run/123",
        "received_at": "2026-05-26T21:00:00Z",
        "title": "CI failed",
        "severity": "error",
        "raw_ref": "helpcenter/inbox/msg-1.raw.json",
        "normalized_summary": "Pytest failed in test_api.py",
        "labels": ["ci", "pytest"],
        "privacy_class": "internal",
        "redaction_status": "not_required",
    }


def _valid_analysis() -> dict:
    return {
        "analysis_id": "a-1",
        "message_id": "msg-1",
        "generated_at": "2026-05-26T21:10:00Z",
        "status": "ready",
        "failure_summary": "Test suite failed",
        "likely_causes": ["test regression"],
        "affected_files": ["tests/test_api.py"],
        "affected_tasks": ["T07.04"],
        "next_steps": ["reproduce locally"],
        "confidence": 0.8,
        "source_refs": ["run/123"],
        "provenance_refs": ["src:github/run/123"],
        "machine_readable_findings": [{"kind": "pytest_failure"}],
        "human_summary": "Failure is isolated to API tests.",
        "no_auto_fix": True,
    }


def test_helpcenter_structure_is_repo_relative_and_creates_readme(tmp_path: Path) -> None:
    created = ensure_helpcenter_structure(repo_root=tmp_path)
    root = tmp_path / "helpcenter"
    assert root.exists()
    assert (root / "README.md").exists()
    assert all((root / item).exists() for item in ("inbox", "reports", "sources", "attachments", "index"))
    assert created["paths"]["reports"] == "reports"


def test_helpcenter_message_schema_accepts_valid_and_rejects_missing_host_fields() -> None:
    assert validate_helpcenter_message(_valid_message()) == []
    invalid = _valid_message()
    invalid.pop("source_ref", None)
    issues = validate_helpcenter_message(invalid)
    assert issues
    assert issues[0]["reason_code"] == "missing_required_field"


def test_helpcenter_analysis_schema_accepts_minimal_and_complete() -> None:
    assert validate_helpcenter_analysis(_valid_analysis()) == []
    invalid = _valid_analysis()
    invalid["no_auto_fix"] = False
    issues = validate_helpcenter_analysis(invalid)
    assert issues


def test_helpcenter_index_tracks_latest_report_by_message_and_updates_file(tmp_path: Path) -> None:
    empty = load_helpcenter_index(repo_root=tmp_path)
    assert empty["schema"] == "helpcenter_index.v1"
    updated = upsert_helpcenter_index_entry(
        empty,
        analysis_id="analysis-1",
        message_id="msg-1",
        status="ready",
        severity="error",
        source_kind="github_workflow_failure",
        created_at="2026-05-26T21:20:00Z",
        report_ref="helpcenter/reports/2026-05-26/analysis-1.md",
    )
    updated = upsert_helpcenter_index_entry(
        updated,
        analysis_id="analysis-2",
        message_id="msg-1",
        status="ready",
        severity="error",
        source_kind="github_workflow_failure",
        created_at="2026-05-26T21:21:00Z",
        report_ref="helpcenter/reports/2026-05-26/analysis-2.md",
    )
    assert validate_helpcenter_index(updated) == []
    assert updated["latest_report_ref"]["msg-1"].endswith("analysis-2.md")
    path = write_helpcenter_index(updated, repo_root=tmp_path)
    disk = json.loads(path.read_text(encoding="utf-8"))
    assert disk["latest_report_ref"]["msg-1"].endswith("analysis-2.md")


def test_helpcenter_report_paths_are_deterministic_by_day(tmp_path: Path) -> None:
    refs = build_report_paths(analysis_id="analysis-9", report_date=date(2026, 5, 26), repo_root=tmp_path)
    assert refs["markdown_ref"] == "helpcenter/reports/2026-05-26/analysis-9.md"
    assert refs["json_ref"] == "helpcenter/reports/2026-05-26/analysis-9.json"


def test_default_index_validates() -> None:
    assert validate_helpcenter_index(default_helpcenter_index()) == []
