from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from agent.services.helpcenter_contract_service import validate_helpcenter_message


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def create_manual_helpcenter_message(
    *,
    title: str,
    text: str,
    severity: str = "warning",
    source_ref: str = "manual://input",
    labels: list[str] | None = None,
) -> dict[str, Any]:
    resolved_title = str(title).strip()
    summary = str(text).strip()
    if not resolved_title:
        raise ValueError("manual_input_title_required")
    if not summary:
        raise ValueError("manual_input_text_required")
    payload = {
        "message_id": f"manual-{uuid4().hex[:12]}",
        "source_kind": "manual_note",
        "source_ref": str(source_ref).strip() or "manual://input",
        "received_at": _now_iso(),
        "title": resolved_title,
        "severity": str(severity).strip() or "warning",
        "normalized_summary": summary[:3000],
        "labels": [str(item).strip() for item in list(labels or []) if str(item).strip()] or ["manual"],
        "privacy_class": "internal",
        "redaction_status": "pending",
    }
    issues = validate_helpcenter_message(payload)
    if issues:
        raise ValueError(f"manual_helpcenter_message_invalid:{issues[0]['reason_code']}")
    return payload
