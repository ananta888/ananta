from __future__ import annotations

from typing import Any


def explain_mail_for_snake_assist(
    *,
    opened: bool,
    artifact_ref: str,
    message_ref: dict[str, Any],
    body_text: str,
) -> dict[str, Any]:
    if not bool(opened):
        return {
            "ok": False,
            "reason_code": "mail_not_opened",
            "answer": "",
            "mail_source_refs": [],
        }
    source_ref = str(artifact_ref or "").strip()
    if not source_ref:
        return {
            "ok": False,
            "reason_code": "mail_not_explicitly_shared",
            "answer": "",
            "mail_source_refs": [],
        }
    ref = dict(message_ref or {})
    subject = str(ref.get("message_id") or "")
    preview = str(body_text or "").strip()[:240]
    answer = (
        "Mail explain mode (single message only): "
        f"source={source_ref}, message_id={subject or '-'}, "
        f"preview={preview or '(no body loaded)'}"
    )
    return {
        "ok": True,
        "reason_code": "mail_explain_ready",
        "answer": answer,
        "mail_source_refs": [source_ref],
        "auto_inbox_summary": False,
    }
