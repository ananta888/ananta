from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

SCHEMA_FILE = Path(__file__).resolve().parents[2] / "schemas" / "artifacts" / "execution_provenance.v1.json"


def _load_schema() -> dict[str, Any]:
    return json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))


def validate_execution_provenance_payload(payload: dict[str, Any]) -> list[str]:
    validator = Draft202012Validator(_load_schema())
    errors = sorted(validator.iter_errors(payload), key=lambda err: list(err.path))
    return [f"{'/'.join(map(str, e.path)) or '$'}: {e.message}" for e in errors]
