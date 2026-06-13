from __future__ import annotations

import json
from pathlib import Path

from agent.services.planning_track_contract_service import (
    build_planning_track_envelope,
    planning_track_profile,
    planning_contract_ref,
    required_optional_field_spec,
    unwrap_planning_track_payload,
    validate_planning_track_payload,
)


def _todo_track_payload() -> dict:
    file_path = (
        Path(__file__).resolve().parents[1]
        / "todos"
        / "archiv"
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


def test_planning_track_profile_exposes_quality_and_snapshot() -> None:
    profile = planning_track_profile(mode="track_planner")
    assert profile["mode"] == "track_planner"
    assert profile["minimum_task_quality"]["required_fields"] == [
        "title",
        "status",
        "priority",
        "risk",
        "type",
        "acceptance_criteria",
    ]
    assert profile["summary_policy"]["validate_tasks_status_summary"] is True
    assert profile["config_snapshot_ref"] == "config:planning_track_profile_v1"


def test_planning_track_envelope_roundtrip_keeps_payload() -> None:
    payload = _todo_track_payload()
    envelope = build_planning_track_envelope(
        payload=payload,
        generated_by="planner-worker",
        model_ref="model:planner",
        prompt_template_ref="prompt:planning/track_planning",
        summary_recalculation_status="recalculated",
        old_summary_hash="old",
        new_summary_hash="new",
    )
    unwrapped, envelope_raw = unwrap_planning_track_payload(envelope)
    assert unwrapped["track"] == payload["track"]
    assert envelope_raw is not None
    assert envelope["validation_status"] == "valid"
    assert envelope["summary_recalculation_status"] == "recalculated"
    assert envelope["old_summary_hash"] == "old"
    assert envelope["new_summary_hash"] == "new"


def test_planning_track_fixtures_validate_against_schema() -> None:
    fixtures = [
        Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "planning_tracks" / "small_track.json",
        Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "planning_tracks" / "large_track.json",
    ]
    for fixture in fixtures:
        payload = json.loads(fixture.read_text(encoding="utf-8"))
        assert validate_planning_track_payload(payload) == []
