from __future__ import annotations

from agent.services.imap_redaction_pipeline_service import redact_mail_for_worker_context


def test_redaction_pipeline_masks_body_and_reports_reason_code() -> None:
    result = redact_mail_for_worker_context(body_text="token=secret-1 password=hunter2")
    assert result["redaction_status"] == "redacted"
    assert result["reason_code"] == "secret_detected"
    assert "hunter2" not in result["redacted_body"]
    assert result["original_body"] == "token=secret-1 password=hunter2"


def test_redaction_pipeline_blocks_attachment_with_secret_metadata() -> None:
    result = redact_mail_for_worker_context(
        body_text="hello",
        attachments=[{"filename": "credentials.txt", "content_type": "text/plain", "size": 12, "text_excerpt": "password=abc"}],
    )
    assert result["reason_code"] == "attachment_secret_detected"
    assert result["attachment_results"][0]["release_decision"] == "blocked"
