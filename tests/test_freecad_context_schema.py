from __future__ import annotations

import json
from pathlib import Path

import jsonschema

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((ROOT / "schemas" / "freecad" / "freecad_context.v1.json").read_text(encoding="utf-8"))


def test_freecad_context_schema_accepts_bounded_payload() -> None:
    payload = {
        "document": {"name": "Assembly.FCStd", "unit": "mm"},
        "objects": [{"name": "Body", "type": "Part::Feature", "visibility": True}],
        "provenance": {"source": "freecad_addon", "captured_at": "2026-04-30T00:00:00Z", "redaction": True},
        "limits": {"max_objects": 256, "max_payload_bytes": 1048576},
    }
    jsonschema.validate(payload, SCHEMA)
