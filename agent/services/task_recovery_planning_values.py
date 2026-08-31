"""Canonical value projections for Hub-owned recovery planning."""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any


def sha256_json(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    serializer = getattr(value, "model_dump", None)
    if callable(serializer):
        dumped = serializer()
        return dict(dumped) if isinstance(dumped, dict) else {}
    return {}


def transitioned_recovery_strategy(
    task: Any,
    *,
    status: str,
    reason_code: str,
) -> dict[str, Any]:
    current: dict[str, Any] = {}
    for field in ("verification_status", "status_reason_details"):
        current.update(mapping(mapping(getattr(task, field, None)).get("model_recovery_strategy")))
    return {
        "schema": "ananta.model_recovery_strategy.v1",
        **current,
        "status": status,
        "reason_code": reason_code,
        "updated_at": time.time(),
    }
