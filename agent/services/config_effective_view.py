"""Effective configuration view: defaults -> DB overrides -> env, per key.

The AGENT_CONFIG merge chain silently lets later layers win, so a DB row can
shadow an environment setting (or the reverse) without any visible trace.
This read model re-runs the chain and attributes every key to the layer that
decides its value, with secrets redacted (SRP: no mutation, no secrets).
"""

from __future__ import annotations

import json
import re
from typing import Any

_SECRET_KEY = re.compile(r"(api[_-]?key|token|secret|password|credential|authorization)", re.IGNORECASE)
_REDACTED = "***"
_RESERVED_KEYS = {"status", "message", "code"}


def _copy(value: Any) -> Any:
    return json.loads(json.dumps(value))


def _redact(value: Any, key_hint: str = "") -> Any:
    key = str(key_hint or "")
    # ``*_profile`` keys name a credential reference, they never carry a secret.
    if _SECRET_KEY.search(key) and not key.endswith("_profile"):
        return _REDACTED
    if isinstance(value, dict):
        return {str(key): _redact(entry, str(key)) for key, entry in value.items()}
    if isinstance(value, list):
        return [_redact(entry, key_hint) for entry in value]
    return value


def _db_values() -> dict:
    import logging

    from agent.repository import config_repo
    from agent.services.config_service import unwrap_config

    values: dict = {}
    try:
        for row in config_repo.get_all():
            if row.key in _RESERVED_KEYS:
                continue
            try:
                values[row.key] = unwrap_config(json.loads(row.value_json))
            except Exception:
                values[row.key] = row.value_json
    except Exception as exc:  # pragma: no cover - diagnostics must not raise
        logging.warning("config_effective_db_read_failed: %s", exc)
    return values


def build_effective_config_view() -> dict:
    """One row per effective key: value (redacted) and the deciding layer."""
    from agent.config_defaults import build_default_agent_config
    from agent.config_defaults_env import apply_env_config_overrides, merge_db_config_overrides

    defaults = build_default_agent_config()
    db_values = _db_values()
    merged = _copy(defaults)
    merge_db_config_overrides(merged)
    with_env = _copy(merged)
    apply_env_config_overrides(with_env)

    entries: dict = {}
    for key in sorted(set(defaults) | set(db_values) | set(with_env)):
        default_value = defaults.get(key)
        db_stage = merged.get(key)
        final_value = with_env.get(key)
        if final_value != db_stage:
            source = "env"
        elif key in db_values and db_stage != default_value:
            source = "db"
        elif key not in defaults:
            source = "db"
        else:
            source = "default"
        entries[key] = {"source": source, "value": _redact(final_value, key)}
    return {"entries": entries, "layers": ["default", "db", "env"]}
