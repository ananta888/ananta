"""Deterministic serialization helpers for signed and hashed contracts."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any


def canonical_json(value: Any) -> str:
    """Serialize JSON data deterministically without environment-specific values."""

    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


_SENSITIVE_PARTS = (
    "api_key",
    "authorization",
    "credential",
    "password",
    "private_key",
    "secret",
    "token",
)
_SAFE_TOKEN_KEYS = frozenset(
    {
        "cached_tokens",
        "fencing_token",
        "input_tokens",
        "max_tokens",
        "output_tokens",
        "reasoning_tokens",
        "token_count",
        "token_usage",
    }
)


def redact_json(value: Any) -> Any:
    """Return a recursively redacted JSON-compatible value.

    Identifiers such as ``token_count`` are retained; credential-like key names are
    redacted. Runtime contracts should still carry references instead of secrets.
    """

    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for raw_key, item in value.items():
            key = str(raw_key)
            lowered = key.lower()
            is_sensitive = lowered not in _SAFE_TOKEN_KEYS and any(
                lowered == part
                or lowered.endswith(f"_{part}")
                or lowered.startswith(f"{part}_")
                for part in _SENSITIVE_PARTS
            )
            result[key] = "[REDACTED]" if is_sensitive else redact_json(item)
        return result
    if isinstance(value, (list, tuple)):
        return [redact_json(item) for item in value]
    return value


def contains_sensitive_keys(value: Any) -> bool:
    return bool(redact_json(value) != value)
