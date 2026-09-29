"""Chat setting validation, redaction, process-ref checks and v3 settings migration."""

from __future__ import annotations

import copy
import re
from typing import Any

from agent.services.chat_process_binding import (
    load_graph,
    normalize_process_ref,
)
from agent.services.chat_session_security import ChatSessionPrincipal
from agent.services.chat_setting_catalog import (
    SettingValidationIssue,
    canonical_setting_contract,
    validate_setting_delta,
)


def _chat_setting_contract() -> tuple[dict[str, Any], dict[str, list[str]]]:
    return canonical_setting_contract()


def _validated_profile_settings(raw: Any, *, allow_null_reset: bool = False):
    if not isinstance(raw, dict):
        return None, [{"key": "settings", "error_code": "invalid_type", "expected": "object", "received": raw}]
    defaults, options = _chat_setting_contract()
    normalized, issues = validate_setting_delta(
        raw,
        defaults=defaults,
        allowed_keys=defaults,
        options=options,
        allow_null_reset=allow_null_reset,
    )
    if normalized.get("chat_backend_model") == "":
        if allow_null_reset:
            normalized["chat_backend_model"] = None
        else:
            normalized.pop("chat_backend_model", None)
    credential_ref = normalized.get("chat_backend_credential_ref")
    if credential_ref and not re.fullmatch(r"env://[A-Z][A-Z0-9_]{1,127}", str(credential_ref)):
        issues.append(
            SettingValidationIssue(
                "chat_backend_credential_ref", "invalid_credential_reference", "env://VARIABLE_NAME", credential_ref
            )
        )
    return normalized, [issue.as_dict() for issue in issues]


def _provider_setting_issues(settings: dict[str, Any]) -> list[dict[str, Any]]:
    backend = str(settings.get("chat_backend") or "ananta-worker")
    if settings.get("chat_backend_api_base") and backend == "ananta-worker":
        return [
            {
                "key": "chat_backend_api_base",
                "error_code": "setting_not_allowed_for_provider",
                "expected": "external provider backend",
                "received": settings["chat_backend_api_base"],
            }
        ]
    return []


def _redact_settings(settings: dict[str, Any]) -> dict[str, Any]:
    return {
        key: ("env://***" if key.endswith("credential_ref") and value else value) for key, value in settings.items()
    }


def _migrate_profile_settings_v3(profiles: list[Any]) -> tuple[list[dict[str, Any]], bool]:
    allowed, _ = _chat_setting_contract()
    migrated: list[dict[str, Any]] = []
    changed = False
    for raw in profiles:
        if not isinstance(raw, dict):
            changed = True
            continue
        profile = copy.deepcopy(raw)
        settings = dict(profile.get("settings") or {})
        legacy = dict(profile.get("legacy_settings") or {})
        unknown = {key: value for key, value in settings.items() if key not in allowed}
        if unknown:
            legacy.update(unknown)
            profile["settings"] = {key: value for key, value in settings.items() if key in allowed}
            profile["legacy_settings"] = legacy
            changed = True
        migrated.append(profile)
    return migrated, changed


def _migrate_session_settings_v3(sessions: list[Any]) -> bool:
    allowed, _ = _chat_setting_contract()
    changed = False
    for session in sessions:
        if not isinstance(session, dict):
            continue
        delta = dict(session.get("settings_delta") or {})
        unknown = {key: value for key, value in delta.items() if key not in allowed}
        if unknown:
            legacy = dict(session.get("legacy_settings_delta") or {})
            legacy.update(unknown)
            session["legacy_settings_delta"] = legacy
            session["settings_delta"] = {key: value for key, value in delta.items() if key in allowed}
            changed = True
        if "process_ref" not in session:
            session["process_ref"] = None
            changed = True
        if "process_runs" not in session:
            session["process_runs"] = []
            changed = True
    return changed


def _validated_process_ref(
    raw: Any,
    principal: ChatSessionPrincipal,
) -> dict[str, str] | None:
    ref = normalize_process_ref(raw)
    if ref is not None and load_graph(
        ref["graph_id"],
        ref["version"],
        tenant_id=principal.tenant_id,
        subject_id=principal.subject_id,
    ) is None:
        raise LookupError("process_definition_not_found")
    return ref
