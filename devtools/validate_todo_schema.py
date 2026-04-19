from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft7Validator


ROOT = Path(__file__).resolve().parents[1]
TODO_PATH = ROOT / "todo.json"
SCHEMA_PATH = ROOT / "todo.schema.json"


def main() -> int:
    todo = json.loads(TODO_PATH.read_text(encoding="utf-8"))
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = Draft7Validator(schema)
    errors = sorted(validator.iter_errors(todo), key=lambda e: list(e.path))
    if errors:
        for err in errors:
            path = ".".join(str(p) for p in err.path) or "<root>"
            print(f"{path}: {err.message}")
        return 1
    print("todo schema validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
