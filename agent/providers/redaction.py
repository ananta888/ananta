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

SAFE_NUMERIC_TOKEN_KEYS = {
    "cached_tokens",
    "completion_tokens",
    "max_completion_tokens",
    "max_context_tokens",
    "max_output_tokens",
    "max_tokens",
    "prompt_tokens",
    "total_tokens",
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


def _is_safe_protocol_token_field(key: str, value: Any) -> bool:
    """Token counts and limits (``max_context_tokens``, ``cached_tokens``, ...) are numbers, not secrets."""
    normalized = str(key or "").strip().lower()
    counted = normalized in SAFE_NUMERIC_TOKEN_KEYS or normalized.endswith("_tokens")
    return counted and (
        value is None
        or (not isinstance(value, bool) and isinstance(value, (int, float)))
    )


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
    _schema_properties: bool = False,
) -> Any:
    """Redact secrets in an outgoing provider payload.

    Keys whose name looks secret (``token``, ``password``, ...) are replaced -- except the
    keys of a JSON-schema ``properties`` map (tool parameters, ``response_format``): there a
    key is a parameter name such as ``max_tokens`` and its value a schema, not a secret.
    Redacting them broke every tool definition with such a parameter (``Unrecognized schema``).
    Values are still checked against the known secret references everywhere.
    """
    markers = _normalize_markers(secret_keys)
    refs = _normalize_refs(secret_refs)

    if isinstance(payload, dict):
        redacted: dict[str, Any] = {}
        for raw_key, raw_value in payload.items():
            key = str(raw_key)
            if (
                not _schema_properties
                and _is_sensitive_key(key, markers)
                and not _is_safe_protocol_token_field(key, raw_value)
            ):
                redacted[key] = replacement
            else:
                redacted[key] = redact_provider_payload(
                    raw_value,
                    secret_keys=markers,
                    secret_refs=refs,
                    replacement=replacement,
                    _schema_properties=key == "properties" and isinstance(raw_value, dict) and not _schema_properties,
                )
        return redacted
    if isinstance(payload, list):
        return [
            redact_provider_payload(item, secret_keys=markers, secret_refs=refs, replacement=replacement)
            for item in payload
        ]
    return _redact_scalar(payload, secret_refs=refs, replacement=replacement)
