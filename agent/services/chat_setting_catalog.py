"""Canonical validation and schema projection for chat settings.

The catalog owns setting semantics; HTTP routes only adapt requests and
responses. Scope-specific default maps remain inputs during the compatibility
migration and can be collapsed into persisted catalog definitions later.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping
from urllib.parse import urlparse

PROFILE_ONLY_DEFAULTS: dict[str, Any] = {"chat_backend_credential_ref": ""}


@dataclass(frozen=True)
class SettingValidationIssue:
    key: str
    error_code: str
    expected: str
    received: Any

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "error_code": self.error_code,
            "expected": self.expected,
            "received": self.received,
        }


def _setting_type(default: Any) -> str:
    if isinstance(default, bool):
        return "boolean"
    if isinstance(default, int):
        return "integer"
    if isinstance(default, float):
        return "number"
    return "string"


def build_setting_schema(
    *,
    global_defaults: Mapping[str, Any],
    session_defaults: Mapping[str, Any],
    options: Mapping[str, Iterable[str]],
) -> dict[str, Any]:
    profile_defaults = {**session_defaults, **PROFILE_ONLY_DEFAULTS}
    keys = sorted(set(global_defaults) | set(profile_defaults))
    settings = []
    for key in keys:
        default = profile_defaults.get(key, global_defaults.get(key))
        allowed = [str(value) for value in options.get(key, [])]
        settings.append(
            {
                "key": key,
                "label": key.replace("_", " ").title(),
                "description": "",
                "group": _group_for_key(key),
                "type": "enum" if allowed else _setting_type(default),
                "default": default,
                "scope_defaults": {
                    "global": global_defaults.get(key),
                    "profile": profile_defaults.get(key),
                    "session": session_defaults.get(key),
                },
                "allowed_values": allowed,
                "scopes": _scopes_for_key(key, global_defaults, profile_defaults, session_defaults),
                "secret": key.endswith(("api_key", "token", "password")),
                "advanced": key.startswith(("rag_iterative_", "chat_full_scan_", "embedding_", "query_reform_")),
                "deprecated": False,
            }
        )
    return {"schema_version": 1, "settings": settings}


def validate_setting_delta(
    values: Mapping[str, Any],
    *,
    defaults: Mapping[str, Any],
    allowed_keys: Iterable[str],
    options: Mapping[str, Iterable[str]],
    allow_null_reset: bool = False,
) -> tuple[dict[str, Any], list[SettingValidationIssue]]:
    allowed = set(allowed_keys)
    normalized: dict[str, Any] = {}
    issues: list[SettingValidationIssue] = []
    for key, value in values.items():
        if key not in allowed:
            issues.append(SettingValidationIssue(key, "unknown_setting", "known setting key", value))
            continue
        if value is None and allow_null_reset:
            normalized[key] = None
            continue
        default = defaults.get(key)
        expected = _setting_type(default)
        converted, valid = _convert_value(value, expected)
        if not valid:
            issues.append(SettingValidationIssue(key, "invalid_type", expected, value))
            continue
        allowed_values = {str(item) for item in options.get(key, [])}
        if allowed_values and str(converted) not in allowed_values:
            issues.append(
                SettingValidationIssue(key, "invalid_value", f"one of {sorted(allowed_values)}", value)
            )
            continue
        if key.endswith("api_base") and converted and not _valid_http_url(str(converted)):
            issues.append(SettingValidationIssue(key, "invalid_url", "http(s) URL", value))
            continue
        normalized[key] = converted
    return normalized, issues


def apply_setting_patch(current: Mapping[str, Any], patch: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(current)
    for key, value in patch.items():
        if value is None:
            result.pop(key, None)
        else:
            result[key] = value
    return result


def _convert_value(value: Any, expected: str) -> tuple[Any, bool]:
    if expected == "boolean":
        return (value, isinstance(value, bool))
    if expected == "integer":
        return (value, isinstance(value, int) and not isinstance(value, bool))
    if expected == "number":
        return (value, isinstance(value, (int, float)) and not isinstance(value, bool))
    return (value, isinstance(value, str))


def _valid_http_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _scopes_for_key(
    key: str, global_defaults: Mapping[str, Any], profile_defaults: Mapping[str, Any], session_defaults: Mapping[str, Any]
) -> list[str]:
    scopes = []
    if key in global_defaults:
        scopes.append("global")
    if key in profile_defaults:
        scopes.append("profile")
    if key in session_defaults:
        scopes.append("session")
    return scopes


def _group_for_key(key: str) -> str:
    if key.startswith(("embedding_", "query_reform_")):
        return "Embedding und Query-Reform"
    if key.startswith(("rag_", "chat_rag_", "chat_retrieval_", "chat_codecompass_")):
        return "RAG und Retrieval"
    if key.startswith("chat_full_scan_"):
        return "Full Scan"
    if key.startswith("chat_history_") or "summary" in key:
        return "Memory und Summary"
    if key.startswith("predictive_guide_"):
        return "Predictive Guide"
    return "Chat"
