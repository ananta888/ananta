from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

_SCHEMA_DIR = Path("schemas/tui")
PROFILE_SCHEMA_FILE = "ai_snake_prediction_profile.v1.json"
BEHAVIOR_EVENT_SCHEMA_FILE = "ai_snake_behavior_event.v1.json"
LEARNED_PATTERN_SCHEMA_FILE = "ai_snake_learned_pattern.v1.json"
TRAINING_BUNDLE_SCHEMA_FILE = "ai_snake_training_bundle.v1.json"


def load_schema(schema_filename: str) -> dict[str, Any]:
    payload = json.loads((_SCHEMA_DIR / schema_filename).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"schema is not an object: {schema_filename}")
    return payload


def validate_payload(payload: dict[str, Any], *, schema_filename: str) -> list[str]:
    validator = Draft202012Validator(load_schema(schema_filename))
    errors = sorted(validator.iter_errors(payload), key=lambda err: list(err.path))
    return [f"{'.'.join(str(p) for p in err.path) or '$'}: {err.message}" for err in errors]


def validate_prediction_profile(payload: dict[str, Any]) -> list[str]:
    return validate_payload(payload, schema_filename=PROFILE_SCHEMA_FILE)


def default_profile(*, profile_id: str = "default", display_name: str = "Default Profile", workspace_ref: str = "local") -> dict[str, Any]:
    return {
        "schema_version": "ai_snake_prediction_profile.v1",
        "profile_id": str(profile_id),
        "display_name": str(display_name),
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
        "workspace_ref": str(workspace_ref),
        "privacy_defaults": {
            "default_privacy_class": "workspace",
            "local_only": True,
        },
        "learning_settings": {
            "enabled": True,
            "paused": False,
            "evidence_min_cases": 3,
        },
        "pattern_refs": [],
        "human_summary": "Leeres Profil ohne gelernte Patterns.",
        "ai_summary": "learning=on; patterns=0; privacy=workspace/local_only",
        "export_policy": {
            "allowed_targets": ["local_file"],
            "default_local_only": True,
        },
    }


def profile_examples() -> list[dict[str, Any]]:
    empty = default_profile()
    normal = default_profile(profile_id="operator-main", display_name="Operator Main", workspace_ref="workspace:default")
    normal["pattern_refs"] = ["p-artifact-explain-001"]
    normal["human_summary"] = "Ein aktives Pattern für Artefakt-Erklärungen."
    normal["ai_summary"] = "learning=on; patterns=1; top=artifact_explain"
    extended = default_profile(profile_id="operator-extended", display_name="Extended", workspace_ref="workspace:extended")
    extended["x_ananta"] = {"notes": "extension-field", "preview_flags": {"use_compaction": True}}
    return [empty, normal, extended]
