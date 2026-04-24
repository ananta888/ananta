from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft7Validator

ROOT = Path(__file__).resolve().parents[1]
TODO_PATH = ROOT / "todo.json"
SCHEMA_PATH = ROOT / "todo.schema.json"
TRACK_SCHEMA_PATH = ROOT / "todo.track.schema.json"


def detect_todo_format(todo_payload: dict[str, Any]) -> str:
    if isinstance(todo_payload.get("tasks"), list) and isinstance(todo_payload.get("milestones"), list):
        return "task_track"
    return "category_meta"


def _schema_for_format(format_name: str) -> Path:
    return TRACK_SCHEMA_PATH if format_name == "task_track" else SCHEMA_PATH


def validate_todo_payload(todo_payload: dict[str, Any]) -> tuple[str, list[Any]]:
    format_name = detect_todo_format(todo_payload)
    schema_path = _schema_for_format(format_name)
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    validator = Draft7Validator(schema)
    errors = sorted(validator.iter_errors(todo_payload), key=lambda e: list(e.path))
    return format_name, errors


def main() -> int:
    todo = json.loads(TODO_PATH.read_text(encoding="utf-8"))
    format_name, errors = validate_todo_payload(todo)
    if errors:
        print(f"todo schema format={format_name} invalid")
        for err in errors:
            path = ".".join(str(p) for p in err.path) or "<root>"
            print(f"{path}: {err.message}")
        return 1
    print(f"todo schema validation passed (format={format_name})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
