from __future__ import annotations

from typing import Any

from agent.services.imap_security_policy_service import redact_mail_content


def redact_mail_for_worker_context(
    *,
    body_text: str,
    attachments: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    original_body = str(body_text or "")
    body_result = redact_mail_content(original_body)
    attachment_rows = [dict(item) for item in list(attachments or []) if isinstance(item, dict)]
    attachment_results: list[dict[str, Any]] = []
    blocked = False
    for row in attachment_rows:
        name = str(row.get("filename") or "")
        snippet = str(row.get("text_excerpt") or "")
        rendered = redact_mail_content(f"{name}\n{snippet}")
        status = "blocked" if rendered.get("redaction_hits", 0) else "clean"
        if status == "blocked":
            blocked = True
        attachment_results.append(
            {
                "filename": name,
                "content_type": str(row.get("content_type") or ""),
                "size": int(row.get("size") or 0),
                "redaction_status": "redacted" if rendered.get("redaction_hits", 0) else "not_required",
                "reason_code": "attachment_secret_detected" if rendered.get("redaction_hits", 0) else "clean",
                "release_decision": "blocked" if status == "blocked" else "allowed",
            }
        )
    body_status = str(body_result.get("redaction_status") or "not_required")
    return {
        "original_body": original_body,
        "redacted_body": str(body_result.get("text") or ""),
        "redaction_status": "redacted" if body_status == "redacted" or blocked else "not_required",
        "reason_code": "secret_detected" if body_status == "redacted" else ("attachment_secret_detected" if blocked else "clean"),
        "attachment_results": attachment_results,
    }
