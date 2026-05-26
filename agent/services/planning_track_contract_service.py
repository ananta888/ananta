from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

_SCHEMA_FILE = Path(__file__).resolve().parents[2] / "todos" / "todo.track.schema.json"
REQUIRED_FIELDS: tuple[str, ...] = (
    "version",
    "owner",
    "track",
    "status_scale",
    "priority_scale",
    "risk_scale",
    "milestones",
    "tasks",
    "tasks_status_summary",
)
TRACK_PLANNER_MODES: tuple[str, ...] = ("planner", "track_planner")


def load_planning_track_schema() -> dict[str, Any]:
    return json.loads(_SCHEMA_FILE.read_text(encoding="utf-8"))


def planning_contract_hash() -> str:
    payload = json.dumps(load_planning_track_schema(), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def planning_contract_ref() -> dict[str, str]:
    return {
        "schema_ref": "todos/todo.track.schema.json",
        "schema_id": str(load_planning_track_schema().get("$id") or ""),
        "schema_hash": planning_contract_hash(),
    }


def validate_planning_track_payload(payload: dict[str, Any]) -> list[str]:
    validator = Draft202012Validator(load_planning_track_schema())
    errors = sorted(validator.iter_errors(payload), key=lambda err: list(err.path))
    return [f"{'/'.join(map(str, e.path)) or '$'}: {e.message}" for e in errors]


def required_optional_field_spec() -> dict[str, list[str]]:
    return {
        "required": list(REQUIRED_FIELDS),
        "optional_examples": [
            "critical_path_tasks",
            "tasks_type_summary",
            "progress_summary",
            "execution_stage_summary",
            "summary_notes",
            "end_summaries",
        ],
    }


def planning_track_profile(*, mode: str = "track_planner") -> dict[str, Any]:
    resolved_mode = str(mode or "track_planner").strip().lower() or "track_planner"
    if resolved_mode not in TRACK_PLANNER_MODES:
        resolved_mode = "track_planner"
    fields = required_optional_field_spec()
    return {
        "profile_id": f"planning-track-{resolved_mode}-v1",
        "mode": resolved_mode,
        "status_scale_default": ["todo", "in_progress", "partial", "blocked", "done"],
        "priority_scale_default": ["P1", "P2", "P3"],
        "risk_scale_default": ["low", "medium", "high"],
        "minimum_task_quality": {
            "required_fields": ["title", "risk", "acceptance_criteria"],
            "optional_fields": ["depends_on", "type", "milestone_id"],
        },
        "summary_policy": {
            "validate_tasks_status_summary": True,
            "recompute_on_repair": True,
        },
        "contract": fields,
        "contract_ref": planning_contract_ref(),
        "config_snapshot_ref": "config:planning_track_profile_v1",
    }


def build_planning_track_envelope(
    *,
    payload: dict[str, Any],
    generated_by: str,
    model_ref: str,
    prompt_template_ref: str,
    validation_errors: list[str] | None = None,
) -> dict[str, Any]:
    errors = [str(item) for item in list(validation_errors or []) if str(item).strip()]
    return {
        "kind": "planning_track",
        "schema": "planning_track_envelope.v1",
        "schema_ref": "todos/todo.track.schema.json",
        "schema_hash": planning_contract_hash(),
        "generated_by": str(generated_by or "").strip() or "planner",
        "model_ref": str(model_ref or "").strip(),
        "prompt_template_ref": str(prompt_template_ref or "").strip(),
        "validation_status": "valid" if not errors else "invalid",
        "validation_errors": errors,
        "payload": dict(payload or {}),
    }


def unwrap_planning_track_payload(candidate: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None]:
    data = dict(candidate or {})
    if str(data.get("kind") or "").strip() == "planning_track" and isinstance(data.get("payload"), dict):
        return dict(data["payload"]), data
    return data, None
