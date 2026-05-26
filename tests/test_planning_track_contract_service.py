from __future__ import annotations

import json
from pathlib import Path

from agent.services.planning_track_contract_service import (
    planning_contract_ref,
    required_optional_field_spec,
    validate_planning_track_payload,
)


def _todo_track_payload() -> dict:
    file_path = (
        Path(__file__).resolve().parents[1]
        / "todos"
        / "todo.operator-tui-three-way-flex-diff-ai-mode.json"
    )
    return json.loads(file_path.read_text(encoding="utf-8"))


def test_planning_contract_ref_points_to_track_schema() -> None:
    ref = planning_contract_ref()
    assert ref["schema_ref"] == "todos/todo.track.schema.json"
    assert ref["schema_id"].endswith("/todo.track.schema.json")


def test_validate_planning_track_payload_accepts_real_track() -> None:
    payload = _todo_track_payload()
    errors = validate_planning_track_payload(payload)
    assert errors == []


def test_validate_planning_track_payload_rejects_missing_required_field() -> None:
    payload = _todo_track_payload()
    payload.pop("tasks", None)
    errors = validate_planning_track_payload(payload)
    assert any("required property" in err and "tasks" in err for err in errors)


def test_validate_planning_track_payload_rejects_missing_task_id() -> None:
    payload = _todo_track_payload()
    payload["tasks"][0].pop("id", None)
    errors = validate_planning_track_payload(payload)
    assert any("tasks/0" in err and "required property" in err and "id" in err for err in errors)


def test_required_optional_field_spec_includes_core_contract() -> None:
    spec = required_optional_field_spec()
    assert "tasks_status_summary" in spec["required"]
    assert "critical_path_tasks" in spec["optional_examples"]
