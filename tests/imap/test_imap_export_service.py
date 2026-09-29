from __future__ import annotations

from pathlib import Path

from agent.services.imap_export_service import export_mail_payload


def _message_ref() -> dict:
    return {
        "account_id": "imap-a",
        "mailbox": "INBOX",
        "uid": 3,
        "message_id": "<m3@example.com>",
        "date": "2026-05-27T00:00:00Z",
        "from": "alice@example.com",
        "to": "team@example.com",
    }


def test_export_mail_json_without_body_by_default(tmp_path: Path) -> None:
    result = export_mail_payload(
        message_ref=_message_ref(),
        header_meta={"subject": "Build"},
        body_text="secret body",
        format_name="json",
        include_body=False,
        export_dir=tmp_path / "exports",
    )
    path = Path(result["export_ref"])
    assert path.exists()
    content = path.read_text(encoding="utf-8")
    assert "secret body" not in content
    assert result["body_included"] is False


def test_export_mail_text_and_eml_include_body_when_enabled(tmp_path: Path) -> None:
    text_result = export_mail_payload(
        message_ref=_message_ref(),
        header_meta={"subject": "Build"},
        body_text="visible body",
        format_name="text",
        include_body=True,
        export_dir=tmp_path / "exports",
    )
    eml_result = export_mail_payload(
        message_ref=_message_ref(),
        header_meta={"subject": "Build"},
        body_text="visible body",
        format_name="eml",
        include_body=True,
        export_dir=tmp_path / "exports",
    )
    assert Path(text_result["export_ref"]).read_text(encoding="utf-8").find("visible body") >= 0
    assert Path(eml_result["export_ref"]).read_text(encoding="utf-8").find("visible body") >= 0
