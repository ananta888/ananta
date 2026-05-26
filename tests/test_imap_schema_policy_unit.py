from __future__ import annotations

from agent.services.imap_contract_service import validate_imap_account_config, validate_mail_artifact, validate_mail_message_ref
from agent.services.imap_mail_context_envelope_service import build_mail_context_envelope


def test_schema_and_policy_units_cover_account_message_artifact_and_default_denied_cloud() -> None:
    account_issues = validate_imap_account_config(
        {
            "account_id": "acc-1",
            "display_name": "Work",
            "host": "imap.example.com",
            "port": 993,
            "username_ref": "user://alice",
            "credential_ref": "secret://imap/alice",
            "auth_mode": "password_app_token",
            "tls_mode": "require_tls",
            "sync_policy": "manual",
            "enabled": True,
        }
    )
    message_ref = {
        "account_id": "acc-1",
        "mailbox": "INBOX",
        "uid": 10,
        "message_id": "<m10@example.com>",
        "date": "2026-05-27T00:00:00Z",
        "from": "alice@example.com",
        "to": "team@example.com",
        "subject_hash": "hash",
    }
    message_issues = validate_mail_message_ref(message_ref)
    artifact_issues = validate_mail_artifact(
        {
            "artifact_kind": "metadata_only",
            "message_ref": message_ref,
            "redaction_status": "not_required",
            "policy_decision_ref": "policy:mail:metadata_only",
        }
    )
    cloud_envelope = build_mail_context_envelope(goal_id="goal-unit-cloud", worker_target="cloud_worker")
    assert account_issues == []
    assert message_issues == []
    assert artifact_issues == []
    assert cloud_envelope["allowed"] is False
    assert cloud_envelope["reason_code"] == "mail_context_default_denied_cloud"


def test_plaintext_credentials_are_rejected_in_account_schema_unit() -> None:
    issues = validate_imap_account_config(
        {
            "account_id": "acc-2",
            "display_name": "Work",
            "host": "imap.example.com",
            "port": 993,
            "username_ref": "user://alice",
            "credential_ref": "secret://imap/alice",
            "auth_mode": "password_app_token",
            "tls_mode": "require_tls",
            "sync_policy": "manual",
            "enabled": True,
            "password": "plaintext",
        }
    )
    assert any(item.get("reason_code") == "plaintext_credentials_forbidden" for item in issues)
