"""Content-free input and envelope validation for Visual Process assistance."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from agent.services.visual_process_assistant_errors import VisualProcessAssistantError


def stable_hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            dict(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def envelope_hash(value: Mapping[str, Any]) -> str:
    payload = dict(value)
    payload.pop("envelope_hash", None)
    return stable_hash(payload)


def required_text(payload: Mapping[str, Any], key: str, *, max_length: int) -> str:
    value = str(payload.get(key) or "").strip()
    if not value or len(value) > max_length or any(ord(char) < 32 for char in value):
        raise VisualProcessAssistantError(f"{key}_invalid")
    return value


def bounded_identifier(value: Any, reason_prefix: str) -> str:
    result = str(value or "")
    if (
        not result
        or result != result.strip()
        or len(result) > 200
        or any(ord(char) < 32 or ord(char) == 127 for char in result)
    ):
        raise VisualProcessAssistantError(f"{reason_prefix}_invalid")
    return result
