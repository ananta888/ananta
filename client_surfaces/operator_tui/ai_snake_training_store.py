from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from client_surfaces.operator_tui.ai_snake_training_data import default_profile, validate_prediction_profile


def training_base_dir() -> Path:
    return Path.home() / ".config" / "ananta" / "ai_snake"


def training_paths() -> dict[str, Path]:
    base = training_base_dir()
    return {
        "base_dir": base,
        "active_profile": base / "prediction_profile.active.json",
        "events_log": base / "prediction_events.jsonl",
        "learned_patterns": base / "learned_patterns.json",
        "exports_dir": base / "exports",
    }


def ensure_training_layout() -> dict[str, Path]:
    paths = training_paths()
    paths["base_dir"].mkdir(parents=True, exist_ok=True)
    paths["exports_dir"].mkdir(parents=True, exist_ok=True)
    if not paths["active_profile"].exists():
        profile = default_profile()
        errors = validate_prediction_profile(profile)
        if errors:
            raise ValueError(f"default training profile invalid: {errors[0]}")
        paths["active_profile"].write_text(json.dumps(profile, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if not paths["learned_patterns"].exists():
        payload = {"schema_version": "ai_snake_learned_pattern.v1-list", "patterns": []}
        paths["learned_patterns"].write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return paths


def read_active_profile() -> dict[str, Any]:
    paths = ensure_training_layout()
    payload = json.loads(paths["active_profile"].read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else default_profile()


def data_path_status() -> str:
    paths = ensure_training_layout()
    return (
        f"ai-data base={paths['base_dir']} "
        f"profile={paths['active_profile']} "
        f"events={paths['events_log']} "
        f"patterns={paths['learned_patterns']} "
        f"exports={paths['exports_dir']}"
    )
