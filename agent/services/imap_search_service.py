from __future__ import annotations

from datetime import datetime
from typing import Any

from agent.services.imap_metadata_store_service import ImapMetadataStore


def _as_datetime(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def search_mail_metadata(
    *,
    store: ImapMetadataStore,
    filters: dict[str, Any] | None = None,
    body_contains: str = "",
    include_body_search: bool = False,
) -> dict[str, Any]:
    candidate = dict(filters or {})
    from_q = str(candidate.get("from") or "").strip().lower()
    to_q = str(candidate.get("to") or "").strip().lower()
    subject_q = str(candidate.get("subject") or "").strip().lower()
    mailbox_q = str(candidate.get("mailbox") or "").strip()
    unread_q = candidate.get("unread")
    starred_q = candidate.get("starred")
    date_from = _as_datetime(str(candidate.get("date_from") or ""))
    date_to = _as_datetime(str(candidate.get("date_to") or ""))
    body_q = str(body_contains or "").strip().lower()

    rows = store.list_messages()
    matches: list[dict[str, Any]] = []
    for row in rows:
        ref = dict(row.get("message_ref") or {})
        headers = dict(row.get("header_meta") or {})
        mailbox = str(ref.get("mailbox") or "")
        if mailbox_q and mailbox != mailbox_q:
            continue
        from_text = str(ref.get("from") or headers.get("from") or "").lower()
        to_text = str(ref.get("to") or headers.get("to") or "").lower()
        subject_text = str(headers.get("subject") or "").lower()
        if from_q and from_q not in from_text:
            continue
        if to_q and to_q not in to_text:
            continue
        if subject_q and subject_q not in subject_text:
            continue
        if unread_q is not None and bool(headers.get("unread")) is not bool(unread_q):
            continue
        if starred_q is not None and bool(headers.get("starred")) is not bool(starred_q):
            continue
        ref_date = _as_datetime(str(ref.get("date") or ""))
        if date_from and ref_date and ref_date < date_from:
            continue
        if date_to and ref_date and ref_date > date_to:
            continue
        if body_q:
            if not include_body_search:
                continue
            scope = str(row.get("body_scope") or "metadata_only")
            if scope not in {"body_excerpt", "full_body"}:
                continue
            if body_q not in str(row.get("body") or "").lower():
                continue
        policy_state = str(row.get("body_scope") or "metadata_only")
        source_ref = f"imap://{ref.get('account_id')}/{ref.get('mailbox')}/{ref.get('uid')}"
        matches.append(
            {
                "message_ref": ref,
                "header_meta": headers,
                "stale": bool(row.get("stale")),
                "policy_state": policy_state,
                "source_ref": source_ref,
            }
        )
    return {
        "results": matches,
        "count": len(matches),
        "body_search_enabled": bool(include_body_search),
    }
