"""Bounded value normalization for the CaseFlow edge/trace projection.

Size limits and pure, side-effect-free sanitizers for identities, scalars,
messages and payload excerpts.  User-visible text is redacted before it is
bounded; nothing here reads workflow state.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from typing import Any

from agent.common.redaction import VisibilityLevel, redact
from agent.services.identity_validation import (
    IdentityValidationError,
    require_canonical_identity,
)

MAX_CASEFLOW_EDGE_TRACE_QUERY_BYTES = 4 * 1024
MAX_CASEFLOW_TRACE_EVENTS = 2048
MAX_CASEFLOW_EDGE_MESSAGES = 64
MAX_CASEFLOW_EDGE_TELEMETRY = 128
MAX_CASEFLOW_EDGE_REFERENCES = 128
MAX_CASEFLOW_MESSAGE_CHARS = 2048
MAX_CASEFLOW_IDENTIFIER_CHARS = 160
MAX_CASEFLOW_REFERENCE_CHARS = 256

_TOKEN_USAGE_KEYS = frozenset(
    {
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "prompt_tokens",
        "completion_tokens",
        "cached_tokens",
        "reasoning_tokens",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
    }
)


def _bounded_payload(value: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if "message" in value:
        message = _bounded_message(value.get("message"))
        if message is not None:
            result["message"] = message
    raw_messages = value.get("messages")
    if isinstance(raw_messages, list):
        messages = [
            message
            for item in raw_messages[:MAX_CASEFLOW_EDGE_MESSAGES]
            if (message := _bounded_message(item)) is not None
        ]
        if messages:
            result["messages"] = messages
    for key, maximum in (
        ("model", 160),
        ("provider", 160),
        ("tool", 160),
        ("tool_name", 160),
        ("error", 512),
        ("reason_code", 512),
        ("last_error", 512),
    ):
        safe = _safe_text(value.get(key), maximum=maximum)
        if safe:
            result[key] = safe
    for key in ("duration_ms", "latency_ms"):
        scalar = _non_negative_number(value.get(key))
        if scalar is not None:
            result[key] = scalar
    cost_micros = _non_negative_integer(value.get("cost_micros"))
    if cost_micros is not None:
        result["cost_micros"] = cost_micros
    token_usage = _bounded_scalar_mapping(value.get("token_usage"))
    if token_usage is not None:
        result["token_usage"] = token_usage
    return result


def _bounded_message(value: Any) -> Any | None:
    if isinstance(value, str):
        return {
            "content": _safe_text(value, maximum=MAX_CASEFLOW_MESSAGE_CHARS),
            "_source_truncated": len(value) > MAX_CASEFLOW_MESSAGE_CHARS,
        }
    if not isinstance(value, Mapping):
        return None
    content = value.get("content") or value.get("text")
    if not isinstance(content, str) or not content:
        return None
    result: dict[str, Any] = {
        "content": _safe_text(content, maximum=MAX_CASEFLOW_MESSAGE_CHARS),
        "_source_truncated": len(content) > MAX_CASEFLOW_MESSAGE_CHARS,
    }
    role = _safe_text(value.get("role"), maximum=64)
    if role:
        result["role"] = role
    return result


def _stable_digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _safe_text(value: Any, *, maximum: int) -> str:
    if value is None:
        return ""
    if isinstance(value, Mapping):
        value = value.get("message") or value.get("reason_code") or ""
    if not isinstance(value, str):
        return ""
    safe = redact(value, VisibilityLevel.USER)
    return str(safe)[:maximum]


def _coalesced_optional_identity(*values: Any) -> str:
    normalized: list[str] = []
    for value in values:
        if value is None or value == "":
            continue
        try:
            normalized.append(
                require_canonical_identity(
                    value,
                    field_name="identity",
                    max_length=MAX_CASEFLOW_IDENTIFIER_CHARS,
                )
            )
        except IdentityValidationError as exc:
            raise ValueError("caseflow_event_identity_invalid") from exc
    if len(set(normalized)) > 1:
        raise ValueError("caseflow_event_identity_conflicting")
    return normalized[0] if normalized else ""


def _optional_reference(value: Any) -> str:
    try:
        return require_canonical_identity(
            value,
            field_name="reference",
            required=False,
            max_length=MAX_CASEFLOW_REFERENCE_CHARS,
        )
    except IdentityValidationError:
        return ""


def _positive_integer(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None


def _non_negative_integer(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result >= 0 else None


def _positive_number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) and result > 0 else None


def _non_negative_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) and result >= 0 else None


def _bounded_scalar_mapping(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    result: dict[str, Any] = {}
    for raw_key, item in sorted(value.items(), key=lambda entry: str(entry[0]))[:16]:
        key = str(raw_key)
        if key not in _TOKEN_USAGE_KEYS or isinstance(item, bool):
            continue
        if isinstance(item, (int, float)) and math.isfinite(float(item)) and item >= 0:
            result[key] = item
    return result or None


def _unique_existing(values: Any) -> list[str]:
    return sorted({value for value in values if value})
