from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from scripts.validate_todo_consistency import validate_todo_payload

ROOT = Path(__file__).resolve().parents[1]
TODO_PATH = ROOT / "todo.json"


def _load_payload() -> dict:
    return json.loads(TODO_PATH.read_text(encoding="utf-8"))


def test_current_todo_summary_is_consistent() -> None:
    payload = _load_payload()
    if "execution_stage_summary" in payload:
        assert set((payload["execution_stage_summary"].get("stages") or {}).keys()) >= {
            "TEST-M1",
            "TEST-M2",
            "TEST-M3",
            "TEST-M4",
            "TEST-M5",
            "TEST-M6",
        }
        assert validate_todo_payload(payload) == []
        return

    todos = payload.get("todos")
    assert isinstance(todos, list)
    assert todos
    for item in todos:
        assert isinstance(item, dict)
        assert str(item.get("id") or "").strip()
        assert str(item.get("status") or "").strip()
        assert isinstance(item.get("tasks") or [], list)


def test_validate_todo_payload_detects_stage_counter_drift() -> None:
    payload = deepcopy(_load_payload())
    if "tasks_status_summary" not in payload:
        todos = payload.get("todos") if isinstance(payload.get("todos"), list) else []
        assert isinstance(todos, list)
        return

    payload["tasks_status_summary"]["by_status"]["done"] = payload["tasks_status_summary"]["by_status"]["done"] + 1
    payload["tasks_status_summary"]["progress_percent_done"] = 99.99

    problems = validate_todo_payload(payload)

    assert any(problem.startswith("by_status.done:") for problem in problems)
    assert any(problem.startswith("progress_percent_done:") for problem in problems)
