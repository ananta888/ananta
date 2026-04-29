from __future__ import annotations

from typing import Any, Iterable

DEFAULT_SECRET_KEY_MARKERS = {
    "access_key",
    "api_key",
    "apikey",
    "authorization",
    "bearer",
    "client_secret",
    "password",
    "private_key",
    "secret",
    "token",
}


def _normalize_markers(secret_keys: Iterable[str] | None) -> set[str]:
    markers = set(DEFAULT_SECRET_KEY_MARKERS)
    for key in list(secret_keys or []):
        normalized = str(key or "").strip().lower()
        if normalized:
            markers.add(normalized)
    return markers


def _normalize_refs(secret_refs: Iterable[str] | None) -> set[str]:
    return {
        str(item or "").strip().lower()
        for item in list(secret_refs or [])
        if str(item or "").strip()
    }


def _is_sensitive_key(key: str, markers: set[str]) -> bool:
    normalized = str(key or "").strip().lower()
    return any(marker in normalized for marker in markers)


def _redact_scalar(value: Any, *, secret_refs: set[str], replacement: str) -> Any:
    if isinstance(value, str) and str(value).strip().lower() in secret_refs:
        return replacement
    return value


def redact_provider_payload(
    payload: Any,
    *,
    secret_keys: Iterable[str] | None = None,
    secret_refs: Iterable[str] | None = None,
    replacement: str = "***REDACTED***",
) -> Any:
    markers = _normalize_markers(secret_keys)
    refs = _normalize_refs(secret_refs)

    if isinstance(payload, dict):
        redacted: dict[str, Any] = {}
        for raw_key, raw_value in payload.items():
            key = str(raw_key)
            if _is_sensitive_key(key, markers):
                redacted[key] = replacement
            else:
                redacted[key] = redact_provider_payload(
                    raw_value,
                    secret_keys=markers,
                    secret_refs=refs,
                    replacement=replacement,
                )
        return redacted
    if isinstance(payload, list):
        return [
            redact_provider_payload(item, secret_keys=markers, secret_refs=refs, replacement=replacement)
            for item in payload
        ]
    return _redact_scalar(payload, secret_refs=refs, replacement=replacement)
