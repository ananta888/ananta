from __future__ import annotations

import json
from pathlib import Path

from agent.services.planning_track_contract_service import build_planning_track_envelope
from agent.services.planning_track_planner_service import (
    build_planner_context_envelope,
    parse_planner_output,
    planner_role_spec,
    render_track_planning_prompt,
)


def _fixture_payload() -> dict:
    fixture = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "planning_tracks" / "small_track.json"
    return json.loads(fixture.read_text(encoding="utf-8"))


def test_planner_role_spec_is_track_only() -> None:
    spec = planner_role_spec(mode="planner")
    assert spec["planner_mode"] == "planner"
    assert spec["supports_track_output"] is True
    assert spec["allows_code_changes"] is False
    assert spec["output_kind"] == "planning_track"


def test_build_planner_context_envelope_filters_denied_artifacts() -> None:
    envelope = build_planner_context_envelope(
        goal_id="goal-1",
        goal_text="Build planner track",
        constraints=["keep deterministic output"],
        available_artifacts=[
            {"source_ref": "artifact:a", "title": "allowed"},
            {"source_ref": "artifact:b", "title": "denied"},
        ],
        allowed_source_refs=["artifact:a"],
        codecompass_refs=["ctx:module:planner"],
    )
    assert len(envelope["available_artifacts"]) == 1
    assert envelope["available_artifacts"][0]["source_ref"] == "artifact:a"
    assert envelope["denied_source_refs"] == ["artifact:b"]
    assert envelope["codecompass_refs"] == ["ctx:module:planner"]
    assert envelope["context_summary"]["source_refs"] == ["artifact:a"]


def test_render_track_planning_prompt_contains_required_sections() -> None:
    context_envelope = build_planner_context_envelope(
        goal_id="goal-1",
        goal_text="Build planner track",
        constraints=["deterministic"],
        available_artifacts=[],
        allowed_source_refs=[],
    )
    prompt = render_track_planning_prompt(goal_text="Build planner track", context_envelope=context_envelope)
    assert "CONTROL:" in prompt
    assert "GOAL:" in prompt
    assert "SCHEMA_REQUIREMENTS:" in prompt
    assert "SUMMARY_POLICY:" in prompt
    assert "single source of truth" in prompt.lower()
    assert "OUTPUT:" in prompt
    assert "no markdown fences" in prompt.lower()


def test_parse_planner_output_accepts_payload_only() -> None:
    payload = _fixture_payload()
    result = parse_planner_output(json.dumps(payload))
    assert result["status"] == "valid"
    assert result["payload"]["track"] == payload["track"]
    assert result["envelope"] is None


def test_parse_planner_output_accepts_envelope_and_rejects_prose() -> None:
    payload = _fixture_payload()
    envelope = build_planning_track_envelope(
        payload=payload,
        generated_by="planner",
        model_ref="model:test",
        prompt_template_ref="prompt:planning/track_planning",
    )
    valid = parse_planner_output(json.dumps(envelope))
    assert valid["status"] == "valid"
    assert valid["envelope"]["kind"] == "planning_track"

    invalid = parse_planner_output("please create a plan with 5 steps")
    assert invalid["status"] == "invalid"
    assert invalid["reason_code"] == "non_json_output"


def test_parse_planner_output_accepts_fenced_json_payload() -> None:
    payload = _fixture_payload()
    fenced = f"```json\n{json.dumps(payload)}\n```"
    result = parse_planner_output(fenced)
    assert result["status"] == "valid"
    assert result["payload"]["track"] == payload["track"]


def test_parse_planner_output_rejects_epics_tasks_shape() -> None:
    invalid = {
        "version": "1.0",
        "owner": "planner",
        "track": "invalid",
        "status_scale": [],
        "priority_scale": [],
        "risk_scale": [],
        "milestones": [],
        "epics": [{"id": "E1", "tasks": [{"id": "T1"}]}],
        "tasks_status_summary": {"total": 0},
    }
    result = parse_planner_output(json.dumps(invalid))
    assert result["status"] == "invalid"
    assert result["reason_code"] == "schema_validation_failed"
