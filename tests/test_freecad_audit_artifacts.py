from __future__ import annotations

import json
from pathlib import Path

import jsonschema

ROOT = Path(__file__).resolve().parents[1]
AUDIT_SCHEMA = json.loads((ROOT / "schemas" / "freecad" / "freecad_audit_event.v1.json").read_text(encoding="utf-8"))
VERIFY_SCHEMA = json.loads((ROOT / "schemas" / "freecad" / "freecad_verification_artifact.v1.json").read_text(encoding="utf-8"))


def test_freecad_audit_event_schema() -> None:
    payload = {"event_type": "macro_execution", "capability": "freecad.macro.execute", "correlation_id": "c1", "status": "blocked"}
    jsonschema.validate(payload, AUDIT_SCHEMA)


def test_freecad_verification_artifact_schema() -> None:
    payload = {"artifact_type": "freecad_verification", "status": "passed", "checks": [{"id": "smoke", "status": "passed"}]}
    jsonschema.validate(payload, VERIFY_SCHEMA)
