from __future__ import annotations

import json
from pathlib import Path

from scripts.todo_status_sync import sync


def test_sync_preserves_explicit_block_and_reports_blocked_remainder(
    tmp_path: Path,
) -> None:
    todo_path = tmp_path / "todo.blocked.json"
    todo_path.write_text(
        json.dumps(
            {
                "tasks": [
                    {
                        "id": "T-1",
                        "type": "test",
                        "status": "done",
                        "progress_percent": 100,
                        "priority": "P1",
                        "risk": "medium",
                    },
                    {
                        "id": "T-2",
                        "type": "test",
                        "status": "blocked",
                        "progress_percent": 60,
                        "blocked_reason": "external evidence missing",
                        "priority": "P1",
                        "risk": "medium",
                    },
                ],
                "critical_path_tasks": ["T-1", "T-2"],
                "milestones": [
                    {"id": "M-1", "task_ids": ["T-1", "T-2"], "status": "partial"}
                ],
                "execution_stage_summary": {
                    "stages": {
                        "S-1": {
                            "scope_task_ids": ["T-1", "T-2"],
                            "total": 0,
                            "todo": 2,
                            "in_progress": 0,
                            "partial": 0,
                            "blocked": 0,
                            "done": 0,
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    assert sync(todo_path) == 0

    synced = json.loads(todo_path.read_text(encoding="utf-8"))
    assert synced["tasks"][1]["status"] == "blocked"
    assert synced["tasks"][1]["blocked_reason"] == "external evidence missing"
    assert synced["progress_summary"]["state"] == "blocked"
    assert synced["status"] == "blocked"
    assert synced["progress_summary"]["todo_remaining"] == 0
    assert synced["tasks_status_summary"]["by_status"] == {
        "todo": 0,
        "in_progress": 0,
        "partial": 0,
        "blocked": 1,
        "done": 1,
    }
    assert synced["milestones"][0]["status"] == "blocked"
    assert synced["execution_stage_summary"]["stages"]["S-1"] == {
        "scope_task_ids": ["T-1", "T-2"],
        "total": 2,
        "todo": 0,
        "in_progress": 0,
        "partial": 0,
        "blocked": 1,
        "done": 1,
    }


def test_sync_keeps_partial_ahead_of_blocked_for_active_work(tmp_path: Path) -> None:
    todo_path = tmp_path / "todo.partial.json"
    todo_path.write_text(
        json.dumps(
            {
                "tasks": [
                    {
                        "id": "T-1",
                        "type": "test",
                        "status": "todo",
                        "progress_percent": 50,
                    },
                    {
                        "id": "T-2",
                        "type": "test",
                        "status": "blocked",
                        "progress_percent": 0,
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    assert sync(todo_path) == 0

    synced = json.loads(todo_path.read_text(encoding="utf-8"))
    assert synced["tasks"][0]["status"] == "partial"
    assert synced["progress_summary"]["state"] == "partial"
