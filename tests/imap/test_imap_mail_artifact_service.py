from __future__ import annotations

from agent.services.imap_mail_artifact_service import get_mail_artifact, list_mail_artifacts, register_mail_artifact


def test_register_mail_artifact_metadata_and_excerpt(tmp_path) -> None:
    message_ref = {
        "account_id": "imap-a",
        "mailbox": "INBOX",
        "uid": 42,
        "message_id": "<m42@example.com>",
    }
    metadata = register_mail_artifact(
        message_ref=message_ref,
        scope="metadata_only",
        redaction_status="not_required",
        policy_decision_ref="policy:mail:metadata_only",
        repo_root=tmp_path,
    )
    excerpt = register_mail_artifact(
        message_ref=message_ref,
        scope="excerpt",
        redaction_status="redacted",
        policy_decision_ref="policy:mail:excerpt",
        excerpt="safe excerpt",
        repo_root=tmp_path,
    )
    assert metadata["artifact_ref"].startswith("mail://imap-a/INBOX/42")
    assert excerpt["artifact_ref"].endswith("scope=excerpt")
    rows = list_mail_artifacts(repo_root=tmp_path)
    assert len(rows) == 2
    loaded = get_mail_artifact(artifact_ref=excerpt["artifact_ref"], repo_root=tmp_path)
    assert loaded is not None
    assert loaded["redaction_status"] == "redacted"
