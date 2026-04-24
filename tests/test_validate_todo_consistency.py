from __future__ import annotations

from scripts.validate_todo_consistency import validate_todo_payload


def _build_payload() -> dict:
    return {
        "tasks": [
            {"id": "A-1", "status": "done", "priority": "P0", "risk": "critical"},
            {"id": "A-2", "status": "todo", "priority": "P1", "risk": "high"},
        ],
        "priority_scale": ["P0", "P1", "P2", "P3"],
        "risk_scale": ["low", "medium", "high", "critical"],
        "critical_path_tasks": ["A-1"],
        "milestones": [{"id": "M1", "status": "in_progress"}],
        "tasks_status_summary": {
            "total": 2,
            "by_status": {"todo": 1, "in_progress": 0, "blocked": 0, "done": 1},
            "progress_percent_done": 50.0,
            "by_priority": {"P0": 1, "P1": 1, "P2": 0, "P3": 0},
            "by_risk": {"low": 0, "medium": 0, "high": 1, "critical": 1},
            "critical_path": {"total": 1, "done": 1, "remaining": 0},
            "milestones": {"total": 1, "todo": 0, "in_progress": 1, "blocked": 0, "done": 0},
        },
        "execution_stage_summary": {
            "stages": {
                "M1": {
                    "scope_task_ids": ["A-1", "A-2"],
                    "total": 2,
                    "done": 1,
                    "todo": 1,
                    "in_progress": 0,
                    "blocked": 0,
                }
            }
        },
    }


def test_validate_todo_payload_returns_no_problems_when_consistent() -> None:
    assert validate_todo_payload(_build_payload()) == []


def test_validate_todo_payload_reports_summary_and_stage_drift() -> None:
    payload = _build_payload()
    payload["tasks_status_summary"]["by_status"]["done"] = 0
    payload["execution_stage_summary"]["stages"]["M1"]["done"] = 0

    problems = validate_todo_payload(payload)

    assert any(problem.startswith("by_status.done:") for problem in problems)
    assert any(problem.startswith("M1.done:") for problem in problems)
