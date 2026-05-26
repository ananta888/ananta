from __future__ import annotations

import json
from pathlib import Path

from agent.services.planning_summary_doctor_service import doctor_file, fix_file, migrate_track_todos


def _small_track_payload() -> dict:
    fixture = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "planning_tracks" / "small_track.json"
    return json.loads(fixture.read_text(encoding="utf-8"))


def test_doctor_detects_legacy_epics_and_returns_convert_preview(tmp_path: Path) -> None:
    payload = {
        "version": "1.0",
        "owner": "tester",
        "track": "legacy",
        "status_scale": ["todo", "in_progress", "partial", "blocked", "done"],
        "priority_scale": ["P1", "P2", "P3"],
        "risk_scale": ["low", "medium", "high"],
        "milestones": [],
        "epics": [{"id": "E1", "title": "Epic", "tasks": [{"title": "Task from epic"}]}],
        "tasks_status_summary": {"total": 0, "by_status": {"todo": 0, "in_progress": 0, "partial": 0, "blocked": 0, "done": 0}},
    }
    target = tmp_path / "legacy.json"
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    report = doctor_file(target)
    assert report["format"] == "legacy_epics"
    assert report["valid"] is False
    assert report["convert_preview"]["tasks_count"] == 1


def test_fix_converts_legacy_epics_to_flat_tasks(tmp_path: Path) -> None:
    payload = {
        "version": "1.0",
        "owner": "tester",
        "track": "legacy",
        "status_scale": ["todo", "in_progress", "partial", "blocked", "done"],
        "priority_scale": ["P1", "P2", "P3"],
        "risk_scale": ["low", "medium", "high"],
        "milestones": [],
        "epics": [{"id": "E1", "title": "Epic", "tasks": [{"title": "Task from epic"}]}],
        "tasks_status_summary": {"total": 0, "by_status": {"todo": 0, "in_progress": 0, "partial": 0, "blocked": 0, "done": 0}},
    }
    target = tmp_path / "legacy.json"
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    result = fix_file(target, write=True)
    assert result["valid"] is True
    repaired = json.loads(target.read_text(encoding="utf-8"))
    assert "epics" not in repaired
    assert isinstance(repaired.get("tasks"), list) and repaired["tasks"]


def test_migrate_track_todos_can_skip_or_convert_legacy_epics(tmp_path: Path) -> None:
    todos = tmp_path / "todos"
    todos.mkdir(parents=True)
    small = _small_track_payload()
    (todos / "small.json").write_text(json.dumps(small, ensure_ascii=False, indent=2), encoding="utf-8")
    legacy = {
        "version": "1.0",
        "owner": "tester",
        "track": "legacy",
        "status_scale": ["todo", "in_progress", "partial", "blocked", "done"],
        "priority_scale": ["P1", "P2", "P3"],
        "risk_scale": ["low", "medium", "high"],
        "milestones": [],
        "epics": [{"id": "E1", "title": "Epic", "tasks": [{"title": "Task from epic"}]}],
        "tasks_status_summary": {"total": 0, "by_status": {"todo": 0, "in_progress": 0, "partial": 0, "blocked": 0, "done": 0}},
    }
    (todos / "legacy.json").write_text(json.dumps(legacy, ensure_ascii=False, indent=2), encoding="utf-8")

    skipped = migrate_track_todos(repo_root=tmp_path, dry_run=True, convert_epics=False)
    skipped_rows = {Path(item["path"]).name: item for item in list(skipped.get("results") or [])}
    assert skipped_rows["legacy.json"]["legacy_epics_detected"] is True
    assert skipped_rows["legacy.json"]["changed"] is False

    converted = migrate_track_todos(repo_root=tmp_path, dry_run=False, convert_epics=True)
    converted_rows = {Path(item["path"]).name: item for item in list(converted.get("results") or [])}
    assert converted_rows["legacy.json"]["changed"] is True
    repaired = json.loads((todos / "legacy.json").read_text(encoding="utf-8"))
    assert isinstance(repaired.get("tasks"), list) and repaired["tasks"]
