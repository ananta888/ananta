from __future__ import annotations

from typing import Any

DEFAULT_MODEL_PROFILES: dict[str, dict[str, Any]] = {
    "intercepted-coder": {
        "behavior": "markdown_prone",
        "repair_eligible": True,
        "preferred_response_format": "json",
    },
    "openai/gpt-4.1-mini": {
        "behavior": "strict_json_good",
        "repair_eligible": False,
        "preferred_response_format": "json",
    },
}


def load_model_profiles(cfg: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    payload = dict(cfg or {})
    provided = payload.get("profiles") if isinstance(payload.get("profiles"), dict) else {}
    merged: dict[str, dict[str, Any]] = {k: dict(v) for k, v in DEFAULT_MODEL_PROFILES.items()}
    for model, profile in provided.items():
        key = str(model or "").strip()
        if not key or not isinstance(profile, dict):
            continue
        base = dict(merged.get(key) or {})
        base.update(profile)
        merged[key] = base
    return merged

