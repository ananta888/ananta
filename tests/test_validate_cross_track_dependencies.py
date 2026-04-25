from __future__ import annotations

import json
from pathlib import Path

from scripts.validate_cross_track_dependencies import validate_cross_track_dependencies


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def test_validate_cross_track_dependencies_reports_missing_file(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "todo.json",
        {
            "tasks": [
                {
                    "id": "A",
                    "title": "task A",
                    "status": "todo",
                    "depends_on": ["todo.kritis.json:K1"],
                }
            ]
        },
    )
    errors = validate_cross_track_dependencies(root_path=tmp_path, track_files=["todo.json"])
    assert any("missing_track_file_reference" in item for item in errors)


def test_validate_cross_track_dependencies_reports_missing_task_id(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "todo.json",
        {
            "tasks": [
                {
                    "id": "A",
                    "title": "task A",
                    "status": "todo",
                    "depends_on": ["todo.kritis.json:K1-AUD-T15"],
                }
            ]
        },
    )
    _write_json(
        tmp_path / "todo.kritis.json",
        {
            "groups": [
                {
                    "id": "KRITIS-G1",
                    "title": "group",
                    "tasks": [{"id": "K1-AUD-T01", "title": "x", "status": "todo"}],
                }
            ]
        },
    )
    errors = validate_cross_track_dependencies(root_path=tmp_path, track_files=["todo.json", "todo.kritis.json"])
    assert any("missing_target_task_id" in item for item in errors)


def test_validate_cross_track_dependencies_reports_cycle(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "todo.a.json",
        {
            "tasks": [
                {
                    "id": "A1",
                    "title": "task A1",
                    "status": "todo",
                    "depends_on": ["todo.b.json:B1"],
                }
            ]
        },
    )
    _write_json(
        tmp_path / "todo.b.json",
        {
            "tasks": [
                {
                    "id": "B1",
                    "title": "task B1",
                    "status": "todo",
                    "depends_on": ["todo.a.json:A1"],
                }
            ]
        },
    )
    errors = validate_cross_track_dependencies(root_path=tmp_path, track_files=["todo.a.json", "todo.b.json"])
    assert any("circular_cross_track_dependency" in item for item in errors)


def test_validate_cross_track_dependencies_reports_archived_reference(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "todo.json",
        {
            "tasks": [
                {
                    "id": "A",
                    "title": "task A",
                    "status": "todo",
                    "depends_on": ["todo_last.json:REF-T01"],
                }
            ]
        },
    )
    _write_json(tmp_path / "todo_last.json", {"tasks": [{"id": "REF-T01", "title": "done", "status": "done"}]})
    errors = validate_cross_track_dependencies(root_path=tmp_path, track_files=["todo.json"])
    assert any("archived_track_reference" in item for item in errors)


def test_validate_cross_track_dependencies_accepts_valid_nested_group_reference(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "todo.json",
        {
            "tasks": [
                {
                    "id": "PG-T07",
                    "title": "review design",
                    "status": "todo",
                    "depends_on": ["todo.kritis.json:K1-AUD-T15"],
                }
            ]
        },
    )
    _write_json(
        tmp_path / "todo.kritis.json",
        {
            "groups": [
                {
                    "id": "KRITIS-P1",
                    "title": "Audit",
                    "tasks": [{"id": "K1-AUD-T15", "title": "contract tests", "status": "todo"}],
                }
            ]
        },
    )
    errors = validate_cross_track_dependencies(root_path=tmp_path, track_files=["todo.json", "todo.kritis.json"])
    assert errors == []

