from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

SCHEMA_FILE = Path(__file__).resolve().parents[2] / "schemas" / "artifacts" / "source_artifact_grant.v1.json"


def _load_schema() -> dict[str, Any]:
    return json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = str(value).replace("Z", "+00:00")
    return datetime.fromisoformat(normalized)


def validate_source_artifact_grant_payload(payload: dict[str, Any]) -> list[str]:
    validator = Draft202012Validator(_load_schema())
    errors = sorted(validator.iter_errors(payload), key=lambda err: list(err.path))
    return [f"{'/'.join(map(str, e.path)) or '$'}: {e.message}" for e in errors]


def is_grant_active(grant: dict[str, Any], *, now: datetime | None = None) -> tuple[bool, str | None]:
    check_at = now or datetime.now(UTC)
    revoked_at = _parse_timestamp(str(grant.get("revoked_at") or ""))
    if revoked_at is not None and revoked_at <= check_at:
        return False, "grant_revoked"
    expires_at = _parse_timestamp(str(grant.get("expires_at") or ""))
    if expires_at is not None and expires_at <= check_at:
        return False, "grant_expired"
    return True, None
