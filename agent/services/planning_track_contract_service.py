from __future__ import annotations

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


def load_planning_track_schema() -> dict[str, Any]:
    return json.loads(_SCHEMA_FILE.read_text(encoding="utf-8"))


def planning_contract_ref() -> dict[str, str]:
    return {
        "schema_ref": "todos/todo.track.schema.json",
        "schema_id": str(load_planning_track_schema().get("$id") or ""),
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

