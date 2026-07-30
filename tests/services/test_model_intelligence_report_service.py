from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.services.model_intelligence_artifact_store import (
    FileSystemModelIntelligenceArtifactStore,
    ModelIntelligenceArtifactStoreError,
)
from agent.services.model_intelligence_report_service import (
    ModelIntelligenceReportError,
    ModelIntelligenceReportSection,
    ModelIntelligenceReportService,
)


def test_report_is_deterministic_redacted_and_volatile_metadata_free() -> None:
    service = ModelIntelligenceReportService()
    sections = [
        ModelIntelligenceReportSection(
            name="static",
            status="available",
            data={"parameter_count": 7, "token": "must-not-leak"},
        ),
        ModelIntelligenceReportSection(
            name="trace",
            status="unsupported",
            reason_code="runtime_does_not_expose_trace",
        ),
    ]

    first = service.render(
        model_identity={"digest": "sha256:" + ("a" * 64), "local_path": "/secret/model"},
        tool_versions={"ananta": "1.0", "safetensors": "0.5"},
        sections=sections,
        volatile_metadata={"created_at": "first", "hostname": "host-a"},
    )
    second = service.render(
        model_identity={"local_path": "/secret/model", "digest": "sha256:" + ("a" * 64)},
        tool_versions={"safetensors": "0.5", "ananta": "1.0"},
        sections=list(reversed(sections)),
        volatile_metadata={"created_at": "second", "hostname": "host-b"},
    )

    assert first.content_digest == second.content_digest
    assert first.canonical_json == second.canonical_json
    assert b"must-not-leak" not in first.canonical_json
    assert b"/secret/model" not in first.canonical_json
    assert b"created_at" not in first.canonical_json
    assert b"<script" not in first.offline_html
    assert b"<link" not in first.offline_html
    assert b"http://" not in first.offline_html
    assert b"https://" not in first.offline_html


def test_report_roundtrip_validates_sections_and_referenced_artifacts(tmp_path: Path) -> None:
    store = FileSystemModelIntelligenceArtifactStore(root=tmp_path / "objects")
    service = ModelIntelligenceReportService(artifact_store=store)
    child = store.put_bytes(
        "tenant-a",
        b"tensor-summary",
        media_type="application/octet-stream",
        artifact_kind="tensor-summary",
    )
    statuses = ("available", "unsupported", "not_run", "failed")
    sections = [
        ModelIntelligenceReportSection(
            name=f"section_{index}",
            status=status,
            reason_code=None if status == "available" else f"{status}_reason",
            artifact_refs=(child,) if status == "available" else (),
        )
        for index, status in enumerate(statuses)
    ]

    rendered = service.render(
        model_identity={"digest": "sha256:" + ("b" * 64)},
        tool_versions={"ananta": "1.0"},
        sections=sections,
    )
    stored = service.persist("tenant-a", rendered)
    loaded = service.load("tenant-a", stored.json_ref)

    assert stored.content_digest == rendered.content_digest
    assert loaded["schema"] == "ananta.model-intelligence-report.v1"
    assert {section["status"] for section in loaded["sections"]} == set(statuses)
    assert loaded["sections"][0]["artifact_refs"][0]["digest"] == child.digest

    with pytest.raises(ModelIntelligenceArtifactStoreError) as error:
        service.load("tenant-b", stored.json_ref)
    assert error.value.reason_code == "artifact_access_denied"


def test_report_rejects_invalid_section_status() -> None:
    with pytest.raises(ModelIntelligenceReportError) as error:
        ModelIntelligenceReportSection(name="trace", status="maybe")

    assert error.value.reason_code == "report_section_status_invalid"
