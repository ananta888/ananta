from __future__ import annotations

from devtools.validate_todo_schema import detect_todo_format, validate_todo_payload


def test_detect_todo_format_task_track() -> None:
    payload = {
        "version": 1,
        "owner": "ananta",
        "track": "sample",
        "status_scale": ["todo", "in_progress", "blocked", "done"],
        "priority_scale": ["P0", "P1"],
        "risk_scale": ["low", "high"],
        "milestones": [{"id": "M1", "title": "Milestone", "task_ids": ["T1"], "status": "todo"}],
        "tasks": [
            {
                "id": "T1",
                "title": "Task",
                "status": "todo",
                "priority": "P0",
                "risk": "high",
                "acceptance_criteria": ["one"],
            }
        ],
        "tasks_status_summary": {
            "total": 1,
            "by_status": {"todo": 1, "in_progress": 0, "blocked": 0, "done": 0},
            "progress_percent_done": 0.0,
            "by_priority": {"P0": 1, "P1": 0},
            "by_risk": {"low": 0, "high": 1},
            "critical_path": {"total": 1, "done": 0, "remaining": 1},
            "milestones": {"total": 1, "todo": 1, "in_progress": 0, "blocked": 0, "done": 0},
        },
    }
    assert detect_todo_format(payload) == "task_track"
    fmt, errors = validate_todo_payload(payload)
    assert fmt == "task_track"
    assert errors == []


def test_detect_todo_format_category_meta() -> None:
    payload = {
        "version": "1",
        "created": "2026-01-01",
        "updated": "2026-01-01",
        "project": "ananta",
        "review_basis": {"reviewed_commit_range": "HEAD", "review_goal": "consistency"},
        "categories": [{"name": "x", "label": "X", "items": []}],
        "meta": {
            "total_items": 0,
            "by_status": {"completed": 0, "partial": 0, "open": 0},
            "notes": ["ok"],
            "recommended_order": ["x"],
        },
    }
    assert detect_todo_format(payload) == "category_meta"
    fmt, errors = validate_todo_payload(payload)
    assert fmt == "category_meta"
    assert errors == []
