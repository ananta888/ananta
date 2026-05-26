from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

SCHEMA_FILE = Path(__file__).resolve().parents[2] / "schemas" / "config" / "config_snapshot.v1.json"
_SECRET_KEYS = {"password", "secret", "token", "api_key", "apikey", "authorization"}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _stable_hash(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _redact_config(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            if str(key).lower() in _SECRET_KEYS:
                redacted[str(key)] = "[REDACTED]"
            else:
                redacted[str(key)] = _redact_config(item)
        return redacted
    if isinstance(value, list):
        return [_redact_config(item) for item in value]
    return value


def _validate(payload: dict[str, Any]) -> list[str]:
    schema = json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(payload), key=lambda err: list(err.path))
    return [f"{'/'.join(map(str, e.path)) or '$'}: {e.message}" for e in errors]


class ConfigSnapshotService:
    def build_snapshot(
        self,
        *,
        config_kind: str,
        source_path_or_ref: str,
        scope: str,
        config_payload: dict[str, Any],
    ) -> dict[str, Any]:
        raw_hash = _stable_hash(config_payload)
        redacted_payload = _redact_config(config_payload)
        redacted_hash = _stable_hash(redacted_payload)
        snapshot = {
            "schema": "config_snapshot.v1",
            "config_snapshot_id": f"cfg-{raw_hash[:16]}",
            "config_kind": str(config_kind),
            "source_path_or_ref": str(source_path_or_ref),
            "scope": str(scope),
            "created_at": _now_iso(),
            "config_hash": raw_hash,
            "redacted_config_hash": redacted_hash,
            "redacted_ref": f"config:redacted:{redacted_hash[:16]}",
        }
        errors = _validate(snapshot)
        if errors:
            raise ValueError(f"invalid_config_snapshot:{'; '.join(errors)}")
        return snapshot
