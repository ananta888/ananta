from __future__ import annotations

from agent.services.imap_snake_assist_service import explain_mail_for_snake_assist


def test_snake_assist_requires_explicit_open_and_shared_mail() -> None:
    not_opened = explain_mail_for_snake_assist(
        opened=False,
        artifact_ref="mail://imap-a/INBOX/1?scope=excerpt",
        message_ref={"message_id": "<m1>"},
        body_text="text",
    )
    not_shared = explain_mail_for_snake_assist(
        opened=True,
        artifact_ref="",
        message_ref={"message_id": "<m1>"},
        body_text="text",
    )
    assert not_opened["ok"] is False
    assert not_opened["reason_code"] == "mail_not_opened"
    assert not_shared["ok"] is False
    assert not_shared["reason_code"] == "mail_not_explicitly_shared"


def test_snake_assist_returns_source_refs_for_single_mail() -> None:
    result = explain_mail_for_snake_assist(
        opened=True,
        artifact_ref="mail://imap-a/INBOX/1?scope=excerpt",
        message_ref={"message_id": "<m1>"},
        body_text="single mail body",
    )
    assert result["ok"] is True
    assert result["auto_inbox_summary"] is False
    assert result["mail_source_refs"] == ["mail://imap-a/INBOX/1?scope=excerpt"]
