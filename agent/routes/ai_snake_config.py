"""T01 + T05: AI-Snake Config Read/Write + Options Endpoint."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from flask import Blueprint, jsonify, request

from agent.auth import check_auth
from agent.services.chat_setting_definitions import _BOOL_KEYS, _DEFAULTS, _OPTIONS, _SCHEMA_KEYS

ai_snake_config_bp = Blueprint("ai_snake_config", __name__)


def _user_json_path() -> Path:
    explicit = os.environ.get("ANANTA_USER_JSON")
    if explicit:
        return Path(explicit).resolve()
    cwd = Path.cwd().resolve()
    if (cwd / ".git").exists() and (cwd / "user.json").exists():
        return cwd / "data" / "user.json"
    return (cwd / "user.json").resolve()


def _seed_user_json_path() -> Path | None:
    explicit = os.environ.get("ANANTA_USER_JSON")
    if explicit:
        return None
    cwd = Path.cwd().resolve()
    seed = cwd / "user.json"
    runtime = _user_json_path()
    if seed.exists() and seed.resolve() != runtime.resolve():
        return seed
    return None


def _load_raw() -> dict[str, Any]:
    """Read the raw file content without schema interpretation."""
    paths = [p for p in (_seed_user_json_path(), _user_json_path()) if p is not None and p.exists()]
    if not paths:
        return {}
    merged: dict[str, Any] = {}
    merged_settings: dict[str, Any] = {}
    saw_settings = False
    last_updated: Any = None
    last_runtime_updated: Any = None
    for p in paths:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                continue
        except Exception:
            continue
        settings = data.get("settings")
        if isinstance(settings, dict):
            saw_settings = True
            merged_settings.update(settings)
            last_updated = data.get("updated", last_updated)
            last_runtime_updated = data.get("_updated_at", last_runtime_updated)
        else:
            merged.update(data)
    if saw_settings:
        merged["settings"] = merged_settings
        if last_updated is not None:
            merged["updated"] = last_updated
        if last_runtime_updated is not None:
            merged["_updated_at"] = last_runtime_updated
    return merged


def _load() -> dict[str, Any]:
    """Read settings, supporting TUI format ({settings: {...}}) and legacy flat format."""
    raw = _load_raw()
    nested = raw.get("settings")
    if isinstance(nested, dict):
        return nested
    return raw


def _save(data: dict[str, Any]) -> None:
    p = _user_json_path()
    tmp = p.with_suffix(".json.tmp")
    try:
        raw = _load_raw()
        # Write into the nested "settings" key if TUI format is present, otherwise flat
        if isinstance(raw.get("settings"), dict):
            raw["settings"].update(data)
        else:
            raw.update(data)
        raw["_updated_at"] = time.time()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, p)
    except Exception:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
        raise


def _current_config() -> dict[str, Any]:
    stored = _load()
    return {k: stored.get(k, _DEFAULTS.get(k)) for k in _SCHEMA_KEYS}


@ai_snake_config_bp.route("/ai-snake/config", methods=["GET"])
@check_auth
def get_ai_snake_config():
    return jsonify({"ok": True, "config": _current_config()})


@ai_snake_config_bp.route("/ai-snake/config", methods=["PATCH"])
@check_auth
def patch_ai_snake_config():
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return jsonify({"ok": False, "error": "expected JSON object"}), 400
    updates: dict[str, Any] = {}
    rejected = []
    for key, raw_value in body.items():
        if key not in _SCHEMA_KEYS:
            rejected.append(key)
            continue
        if key in _BOOL_KEYS:
            updates[key] = bool(raw_value)
        elif isinstance(_DEFAULTS.get(key), (int, float)) and not isinstance(_DEFAULTS.get(key), bool):
            try:
                updates[key] = type(_DEFAULTS[key])(raw_value)
            except (TypeError, ValueError):
                rejected.append(key)
        else:
            updates[key] = str(raw_value) if raw_value is not None else ""
    if not updates and rejected:
        return jsonify({"ok": False, "error": "no valid keys", "rejected": rejected}), 422
    # Global defaults and live-session overrides are separate scopes. Persisting
    # one must not silently mutate the other.
    _save(updates)
    return jsonify({"ok": True, "saved": list(updates.keys()), "rejected": rejected})


@ai_snake_config_bp.route("/ai-snake/config/options", methods=["GET"])
@check_auth
def get_ai_snake_config_options():
    return jsonify({"ok": True, "options": _OPTIONS, "defaults": _DEFAULTS, "bool_keys": list(_BOOL_KEYS)})
