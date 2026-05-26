from __future__ import annotations

from agent.services.imap_contract_service import (
    validate_imap_account_config,
    validate_mail_artifact,
    validate_mail_message_ref,
)


def _valid_account() -> dict:
    return {
        "account_id": "acc-1",
        "display_name": "Work",
        "host": "imap.example.com",
        "port": 993,
        "username_ref": "user://acc-1",
        "credential_ref": "secret://imap/acc-1",
        "auth_mode": "password_app_token",
        "tls_mode": "require_tls",
        "sync_policy": "manual",
        "enabled": True,
    }


def _message_ref() -> dict:
    return {
        "account_id": "acc-1",
        "mailbox": "INBOX",
        "uid": 11,
        "message_id": "<abc@example.com>",
        "date": "2026-05-27T00:00:00Z",
        "from": "sender@example.com",
        "to": "team@example.com",
        "subject_hash": "subjhash",
    }


def test_imap_account_schema_accepts_valid_config() -> None:
    assert validate_imap_account_config(_valid_account()) == []


def test_imap_account_schema_rejects_missing_host() -> None:
    account = _valid_account()
    account.pop("host", None)
    issues = validate_imap_account_config(account)
    assert issues
    assert issues[0]["reason_code"] == "missing_required_field"


def test_imap_account_schema_rejects_plaintext_credentials() -> None:
    account = _valid_account()
    account["password"] = "plaintext"
    issues = validate_imap_account_config(account)
    assert any(item.get("reason_code") == "plaintext_credentials_forbidden" for item in issues)


def test_mail_message_ref_schema_accepts_minimum_payload() -> None:
    assert validate_mail_message_ref(_message_ref()) == []


def test_mail_artifact_schema_accepts_metadata_and_excerpt() -> None:
    metadata_only = {
        "artifact_kind": "metadata_only",
        "message_ref": _message_ref(),
        "redaction_status": "not_required",
        "policy_decision_ref": "policy://1",
        "source_artifact_grant_ref": "grant://mail/1",
    }
    body_excerpt = {
        "artifact_kind": "body_excerpt",
        "message_ref": _message_ref(),
        "redaction_status": "redacted",
        "policy_decision_ref": "policy://2",
        "excerpt": "safe excerpt",
    }
    assert validate_mail_artifact(metadata_only) == []
    assert validate_mail_artifact(body_excerpt) == []
