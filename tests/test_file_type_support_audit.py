from __future__ import annotations

import json
from pathlib import Path

from agent.services.file_type_support_service import FileTypeSupportFilter
from ananta_contracts.file_type_support import load_file_type_support_registry
from scripts.audit_codecompass_file_type_support import (
    _evidence_errors,
    _table,
    audit_repository,
    main,
    probe_runtime_requirements,
)

ROOT = Path(__file__).resolve().parents[1]


def test_audit_counts_tracked_files_by_classifier_stage(tmp_path: Path) -> None:
    (tmp_path / "Dockerfile").write_text("FROM python:3\n", encoding="utf-8")
    (tmp_path / "app.component.html").write_text("<main></main>\n", encoding="utf-8")
    (tmp_path / "run-tool").write_text("#!/usr/bin/env bash\necho ok\n", encoding="utf-8")
    (tmp_path / "unknown.bin").write_bytes(b"\0binary")
    registry = load_file_type_support_registry(ROOT)

    result = audit_repository(
        tmp_path,
        registry,
        tracked_paths=("Dockerfile", "app.component.html", "run-tool", "unknown.bin"),
    )

    assert result.tracked_files == 4
    assert result.classified_files == 3
    assert result.inventory == {"dockerfile": 1, "html": 1, "shell": 1}
    assert result.unclassified_files == ("unknown.bin",)
    assert result.match_kinds == {"exact_filename": 1, "compound_suffix": 1, "shebang": 1}


def test_runtime_probe_covers_every_declared_requirement() -> None:
    registry = load_file_type_support_registry(ROOT)
    expected = {
        requirement
        for descriptor in registry.descriptors
        for pipeline in registry.pipelines
        for dimension in ("indexed", "symbols", "relationships")
        for requirement in descriptor.support_for(pipeline).capability(dimension).runtime_requirements
    }

    assert set(probe_runtime_requirements(registry)) == expected


def test_evidence_audit_reports_missing_file(tmp_path: Path) -> None:
    registry = load_file_type_support_registry(ROOT)

    errors = _evidence_errors(tmp_path, registry)

    assert errors
    assert all(item.startswith("missing evidence: ") for item in errors)


def test_audit_support_matrix_filters_are_deterministic_and_keep_selectors_separate() -> None:
    registry = load_file_type_support_registry(ROOT)
    support_filter = FileTypeSupportFilter.build(
        priorities=["p0"],
        pipelines=["rag_helper"],
        missing_parser=True,
    )

    first = audit_repository(ROOT, registry, tracked_paths=(), support_filter=support_filter)
    second = audit_repository(ROOT, registry, tracked_paths=(), support_filter=support_filter)

    assert first.support_matrix == second.support_matrix
    assert first.support_matrix["runtime_scope"] == "audit_process"
    assert _table(first) == _table(second)
    assert "ext=" in _table(first)
    assert "exact=" in _table(first)
    assert "patterns=" in _table(first)
    assert "shebangs=" in _table(first)
    assert first.support_matrix["filters"]["priority"] == ["P0"]
    assert first.support_matrix["rows"]
    for row in first.support_matrix["rows"]:
        assert row["priority"] == "P0"
        assert row["pipeline"] == "rag_helper"
        assert row["missing_parser"] is True
        assert set(row["selectors"]) == {
            "extensions",
            "exact_filenames",
            "patterns",
            "compound_suffixes",
            "shebangs",
            "text_fallback",
        }


def test_audit_cli_json_applies_support_filters(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        "scripts.audit_codecompass_file_type_support.tracked_repository_paths",
        lambda _root: (),
    )

    exit_code = main(
        [
            "--repository-root",
            str(ROOT),
            "--output",
            "json",
            "--priority",
            "P2",
            "--pipeline",
            "repository_map",
            "--missing-parser",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    rows = payload["support_matrix"]["rows"]
    assert rows
    assert all(row["priority"] == "P2" for row in rows)
    assert all(row["pipeline"] == "repository_map" for row in rows)
    assert all(row["missing_parser"] is True for row in rows)
