from __future__ import annotations

import re
from typing import Any

TOKEN_PATTERNS = [
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bgh[op]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\b(?:api[_-]?key|token|secret)\s*[:=]\s*[^\s,;]+", re.IGNORECASE),
]
ABS_PATH_PATTERN = re.compile(r"(?:(?<=\s)|^)(?:[A-Za-z]:\\|/)(?:[^\\/\s]+[\\/])*[^\\/\s]+")

SENSITIVE_ENV_KEYS = {
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GITHUB_TOKEN",
    "COPILOT_TOKEN",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_ACCESS_KEY_ID",
    "HF_TOKEN",
    "AZURE_OPENAI_API_KEY",
}


def redact_text(text: str) -> str:
    redacted = str(text or "")
    for pattern in TOKEN_PATTERNS:
        redacted = pattern.sub("[REDACTED_TOKEN]", redacted)
    redacted = ABS_PATH_PATTERN.sub("[REDACTED_PATH]", redacted)
    return redacted


def redact_payload(payload: Any) -> Any:
    if isinstance(payload, str):
        return redact_text(payload)
    if isinstance(payload, list):
        return [redact_payload(item) for item in payload]
    if isinstance(payload, dict):
        redacted: dict[str, Any] = {}
        for key, value in payload.items():
            normalized_key = str(key).upper()
            if normalized_key in SENSITIVE_ENV_KEYS:
                redacted[str(key)] = "[REDACTED_ENV]"
            else:
                redacted[str(key)] = redact_payload(value)
        return redacted
    return payload


def sanitize_subprocess_environment(
    environment: dict[str, str] | None,
    *,
    explicitly_allowed_sensitive_keys: set[str] | None = None,
) -> dict[str, str]:
    allowed = {str(item).upper() for item in set(explicitly_allowed_sensitive_keys or set())}
    sanitized: dict[str, str] = {}
    for key, value in dict(environment or {}).items():
        normalized_key = str(key).strip()
        if not normalized_key:
            continue
        if normalized_key.upper() in SENSITIVE_ENV_KEYS and normalized_key.upper() not in allowed:
            continue
        sanitized[normalized_key] = str(value)
    return sanitized


def find_unredacted_secret_markers(text: str) -> list[str]:
    candidates: list[str] = []
    source = str(text or "")
    for pattern in TOKEN_PATTERNS:
        for match in pattern.findall(source):
            token = match if isinstance(match, str) else "".join(str(item) for item in match if item)
            if token:
                candidates.append(token)
    return candidates


def enforce_redaction_gate(text: str) -> tuple[bool, list[str]]:
    matches = find_unredacted_secret_markers(text)
    return (not matches, matches)
