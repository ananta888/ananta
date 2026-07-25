from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping

from agent.services.mail_metadata_store_service import MailMetadataStore


def _date(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def search_mail_metadata(
    *,
    store: MailMetadataStore,
    filters: Mapping[str, Any] | None = None,
    body_contains: str = "",
    include_body_search: bool = False,
) -> dict[str, Any]:
    query = dict(filters or {})
    matches: list[dict[str, Any]] = []
    body_query = str(body_contains).strip().lower()
    for row in store.list_messages(account_id=str(query.get("account_id")) if query.get("account_id") else None):
        ref = dict(row.get("message_ref") or {})
        metadata = dict(row.get("metadata") or {})
        if query.get("from") and str(query["from"]).lower() not in str(metadata.get("from") or "").lower():
            continue
        if query.get("to") and str(query["to"]).lower() not in " ".join(metadata.get("to") or []).lower():
            continue
        if query.get("subject") and str(query["subject"]).lower() not in str(metadata.get("subject") or "").lower():
            continue
        if query.get("mailbox"):
            mailboxes = {str(item) for item in list(metadata.get("mailbox_ref_ids") or [])}
            if str(query["mailbox"]) not in mailboxes:
                continue
        keywords = {str(item) for item in list(metadata.get("keywords") or [])}
        if query.get("unread") is not None and ("$seen" not in keywords) is not bool(query["unread"]):
            continue
        if query.get("starred") is not None and ("$flagged" in keywords) is not bool(query["starred"]):
            continue
        message_date = _date(str(metadata.get("date") or ""))
        lower = _date(str(query.get("date_from") or "")) if query.get("date_from") else None
        upper = _date(str(query.get("date_to") or "")) if query.get("date_to") else None
        if lower and message_date and message_date < lower:
            continue
        if upper and message_date and message_date > upper:
            continue
        if body_query:
            if not include_body_search or str(row.get("body_scope")) not in {"body_excerpt", "full_body"}:
                continue
            body = dict(row.get("body") or {})
            if body_query not in f"{body.get('text', '')} {body.get('html', '')}".lower():
                continue
        mail_ref_id = str(ref.get("mail_ref_id") or "")
        matches.append(
            {
                "mail_ref_id": mail_ref_id,
                "metadata": metadata,
                "stale": bool(row.get("stale")),
                "policy_state": str(row.get("body_scope") or "metadata_only"),
                "source_ref": f"mail://{mail_ref_id}",
            }
        )
    return {"results": matches, "count": len(matches), "body_search_enabled": bool(include_body_search)}
