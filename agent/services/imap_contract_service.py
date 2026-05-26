from __future__ import annotations

from typing import Any

from jsonschema import Draft202012Validator

_PLAINTEXT_CREDENTIAL_KEYS = {"password", "token", "access_token", "refresh_token", "secret"}


def _collect_schema_issues(schema: dict[str, Any], payload: dict[str, Any]) -> list[dict[str, str]]:
    validator = Draft202012Validator(schema)
    issues: list[dict[str, str]] = []
    for error in sorted(validator.iter_errors(payload), key=lambda err: list(err.path)):
        path = "/".join(map(str, error.path)) or "$"
        reason_code = "missing_required_field" if error.validator == "required" else "schema_validation_error"
        issues.append({"path": path, "reason_code": reason_code, "human_message": str(error.message)})
    return issues


def imap_account_schema() -> dict[str, Any]:
    return {
        "$id": "https://ananta.dev/schemas/imap-account-v1.json",
        "type": "object",
        "required": [
            "account_id",
            "display_name",
            "host",
            "port",
            "username_ref",
            "credential_ref",
            "auth_mode",
            "tls_mode",
            "sync_policy",
            "enabled",
        ],
        "additionalProperties": True,
        "properties": {
            "account_id": {"type": "string", "minLength": 1},
            "display_name": {"type": "string", "minLength": 1},
            "host": {"type": "string", "minLength": 1},
            "port": {"type": "integer", "minimum": 1, "maximum": 65535},
            "username_ref": {"type": "string", "minLength": 1},
            "credential_ref": {"type": "string", "minLength": 1},
            "auth_mode": {"type": "string", "enum": ["password_app_token", "oauth2_placeholder"]},
            "tls_mode": {"type": "string", "enum": ["require_tls", "starttls", "none"]},
            "sync_policy": {"type": "string", "enum": ["manual", "headers_only", "limited_recent"]},
            "enabled": {"type": "boolean"},
        },
    }


def mail_message_ref_schema() -> dict[str, Any]:
    return {
        "$id": "https://ananta.dev/schemas/mail-message-ref-v1.json",
        "type": "object",
        "required": ["account_id", "mailbox", "uid", "message_id", "date", "from", "to", "subject_hash"],
        "additionalProperties": True,
        "properties": {
            "account_id": {"type": "string", "minLength": 1},
            "mailbox": {"type": "string", "minLength": 1},
            "uid": {"type": "integer", "minimum": 1},
            "message_id": {"type": "string", "minLength": 1},
            "date": {"type": "string", "minLength": 1},
            "from": {"type": "string", "minLength": 1},
            "to": {"type": "string", "minLength": 1},
            "subject_hash": {"type": "string", "minLength": 1},
            "content_hash": {"type": "string"},
        },
    }


def mail_artifact_schema() -> dict[str, Any]:
    return {
        "$id": "https://ananta.dev/schemas/mail-artifact-v1.json",
        "type": "object",
        "required": ["artifact_kind", "message_ref", "redaction_status", "policy_decision_ref"],
        "additionalProperties": True,
        "properties": {
            "artifact_kind": {
                "type": "string",
                "enum": ["metadata_only", "body_excerpt", "full_body", "attachment_ref"],
            },
            "message_ref": {"type": "object"},
            "redaction_status": {"type": "string", "enum": ["not_required", "pending", "redacted", "blocked"]},
            "policy_decision_ref": {"type": "string", "minLength": 1},
            "excerpt": {"type": "string"},
            "attachment_ref": {"type": "string"},
            "source_artifact_grant_ref": {"type": "string"},
        },
    }


def validate_imap_account_config(payload: dict[str, Any]) -> list[dict[str, str]]:
    candidate = dict(payload or {})
    issues = _collect_schema_issues(imap_account_schema(), candidate)
    for key in _PLAINTEXT_CREDENTIAL_KEYS:
        value = candidate.get(key)
        if isinstance(value, str) and value.strip():
            issues.append(
                {
                    "path": key,
                    "reason_code": "plaintext_credentials_forbidden",
                    "human_message": f"{key} must not be stored in account payload",
                }
            )
    if str(candidate.get("tls_mode") or "") != "require_tls":
        issues.append(
            {
                "path": "tls_mode",
                "reason_code": "tls_mode_must_require_tls",
                "human_message": "tls_mode must default to require_tls",
            }
        )
    return issues


def validate_mail_message_ref(payload: dict[str, Any]) -> list[dict[str, str]]:
    return _collect_schema_issues(mail_message_ref_schema(), dict(payload or {}))


def validate_mail_artifact(payload: dict[str, Any]) -> list[dict[str, str]]:
    candidate = dict(payload or {})
    issues = _collect_schema_issues(mail_artifact_schema(), candidate)
    message_ref = dict(candidate.get("message_ref") or {})
    msg_issues = validate_mail_message_ref(message_ref)
    if msg_issues:
        issues.append(
            {
                "path": "message_ref",
                "reason_code": msg_issues[0]["reason_code"],
                "human_message": msg_issues[0]["human_message"],
            }
        )
    return issues
