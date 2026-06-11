from __future__ import annotations

import json
import re
from typing import Any

from agent.common.audit import _sanitize_details
from agent.services.evolution.models import EvolutionPolicy


def bounded_payload(payload: Any, *, policy: EvolutionPolicy) -> Any:
    if payload is None:
        return None
    sanitized = sanitize_persisted_payload(payload)
    try:
        encoded = json.dumps(sanitized, ensure_ascii=True, sort_keys=True, default=str)
    except TypeError:
        sanitized = {"value": str(sanitized)}
        encoded = json.dumps(sanitized, ensure_ascii=True, sort_keys=True)
    if len(encoded.encode("utf-8")) <= policy.max_raw_payload_bytes:
        return sanitized
    semantic_preview = semantic_payload_preview(sanitized)
    if semantic_preview:
        return {
            "_truncated": True,
            "max_raw_payload_bytes": policy.max_raw_payload_bytes,
            **semantic_preview,
        }
    preview = encoded[: policy.max_raw_payload_bytes]
    return {
        "_truncated": True,
        "max_raw_payload_bytes": policy.max_raw_payload_bytes,
        "preview": preview,
    }


def semantic_payload_preview(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    keep_keys = (
        "run_id",
        "id",
        "status",
        "summary",
        "message",
        "source",
        "evolver_run_id",
        "evolver_status",
    )
    preview = {key: payload[key] for key in keep_keys if key in payload}
    for key in ("proposals", "improvements", "candidates", "events", "validation_results", "validations"):
        value = payload.get(key)
        if isinstance(value, list):
            preview[f"{key}_count"] = len(value)
    if preview:
        preview["preview_keys"] = sorted(str(key) for key in payload.keys())[:30]
    return {"semantic_preview": preview} if preview else {}


def sanitize_persisted_payload(payload: Any) -> Any:
    sanitized = _sanitize_details(payload) if isinstance(payload, dict) else payload
    return redact_persisted_value(sanitized)


def redact_persisted_value(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if is_sensitive_persisted_key(key_text):
                redacted[key] = "***REDACTED***"
            else:
                redacted[key] = redact_persisted_value(item)
        return redacted
    if isinstance(value, list):
        return [redact_persisted_value(item) for item in value]
    if isinstance(value, str):
        return redact_persisted_text(value)
    return value


def is_sensitive_persisted_key(key: str) -> bool:
    normalized = key.strip().lower().replace("-", "_")
    sensitive_fragments = ("token", "secret", "password", "api_key", "apikey", "credential", "authorization")
    if any(fragment in normalized for fragment in sensitive_fragments):
        return True
    return normalized in {"headers", "request_headers", "response_headers", "auth"}


def redact_persisted_text(value: str) -> str:
    redacted = value
    redacted = re.sub(
        r"https?://(?:localhost|127\.0\.0\.1|0\.0\.0\.0|host\.docker\.internal)(?::\d+)?[^\s,\]\)\"]*",
        "***REDACTED_LOCAL_URL***",
        redacted,
        flags=re.IGNORECASE,
    )
    redacted = re.sub(
        r"(?<![\w.:-])(?:/[A-Za-z0-9._ -]+){2,}",
        "***REDACTED_PATH***",
        redacted,
    )
    redacted = re.sub(
        r"(?<![\w.-])[A-Za-z]:\\(?:[^\\/:*?\"<>|\r\n]+\\?){2,}",
        "***REDACTED_PATH***",
        redacted,
    )
    return redacted
