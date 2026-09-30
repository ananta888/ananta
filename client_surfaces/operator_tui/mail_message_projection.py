"""Pure, presentation-side projections of mail metadata rows for the operator TUI.

Normalizes provider-neutral message references and header metadata, applies
list filters and thread counts, derives account states and parses search
queries. No provider access and no content release happen here; those stay
behind ``MailApplicationService`` (SRP)."""
from __future__ import annotations

from typing import Any, Mapping, Sequence


def json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): json_safe(item)
            for key, item in value.items()
            if str(key).lower()
            not in {"body", "content", "raw", "data", "credential_ref", "password", "token"}
        }
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, bytes):
        return {"content_omitted": True, "size": len(value)}
    return value


def message_ref(row: Mapping[str, Any]) -> dict[str, Any]:
    source = {**dict(row.get("message_ref") or {}), **dict(row)}
    protocol = str(source.get("protocol") or "imap").lower()
    mail_ref_id = str(source.get("mail_ref_id") or "").strip()
    ref: dict[str, Any] = {
        "mail_ref_id": mail_ref_id,
        "account_id": str(source.get("account_id") or ""),
        "protocol": protocol,
    }
    thread_ref = str(source.get("thread_ref_id") or "").strip()
    if thread_ref:
        ref["thread_ref_id"] = thread_ref
    message_id = str(source.get("message_id_header") or source.get("message_id") or "").strip()
    if message_id:
        ref["message_id"] = message_id
    # Legacy locators are input compatibility only. JMAP provider locators never
    # cross the surface boundary.
    if protocol == "imap":
        mailbox = str(source.get("mailbox") or "").strip()
        uid = source.get("uid")
        if mailbox:
            ref["mailbox"] = mailbox
        if uid is not None:
            ref["uid"] = uid
    return ref


def header_meta(row: Mapping[str, Any]) -> dict[str, Any]:
    source = {**dict(row.get("header_meta") or {}), **dict(row)}
    raw_to = source.get("to_addresses") or source.get("to") or []
    to_addresses = [raw_to] if isinstance(raw_to, str) else list(raw_to)
    header = {
        "subject": str(source.get("subject") or ""),
        "from": str(source.get("from_address") or source.get("from") or ""),
        "to": to_addresses,
        "date": str(source.get("date") or ""),
        "unread": bool(source.get("unread", False)),
        "size": int(source.get("size") or 0),
    }
    message_id = str(source.get("message_id_header") or source.get("message_id") or "").strip()
    if message_id:
        header["message_id"] = message_id
    return header


def normalize_message(row: Mapping[str, Any]) -> dict[str, Any]:
    source = {**dict(row.get("message_ref") or {}), **dict(row.get("header_meta") or {}), **dict(row)}
    ref = message_ref(source)
    return {
        "mail_ref_id": str(ref.get("mail_ref_id") or ""),
        "message_ref": ref,
        "header_meta": header_meta(source),
        "mailbox_ref_ids": [
            str(item)
            for item in list(source.get("mailbox_ref_ids") or [])
            if str(item).strip()
        ],
        "keywords": dict(source.get("keywords") or {}),
        "stale": bool(source.get("stale", False)),
        "body_scope": "metadata_only",
        "source_ref": str(source.get("source_ref") or ""),
        "attachments": [
            dict(item)
            for item in list(source.get("attachments") or [])
            if isinstance(item, Mapping)
        ],
    }


def mail_message_key(row: Mapping[str, Any]) -> str:
    ref = message_ref(row)
    mail_ref_id = str(ref.get("mail_ref_id") or "").strip()
    if mail_ref_id:
        return mail_ref_id
    message_id = str(ref.get("message_id") or "").strip()
    if message_id:
        return message_id
    return f"{ref.get('account_id')}::{ref.get('mailbox')}::{ref.get('uid')}"


def mailboxes(row: Mapping[str, Any]) -> set[str]:
    normalized = normalize_message(row)
    values = {str(item) for item in normalized.get("mailbox_ref_ids") or [] if str(item)}
    legacy = str(dict(normalized.get("message_ref") or {}).get("mailbox") or "").strip()
    if legacy:
        values.add(legacy)
    return values


def matches_filters(row: Mapping[str, Any], filters: Mapping[str, Any]) -> bool:
    normalized = normalize_message(row)
    ref = dict(normalized.get("message_ref") or {})
    header = dict(normalized.get("header_meta") or {})
    mailbox = str(filters.get("mailbox") or "")
    if mailbox and mailbox not in mailboxes(normalized):
        return False
    if filters.get("from") and str(filters["from"]).casefold() not in str(header.get("from") or "").casefold():
        return False
    if filters.get("to") and str(filters["to"]).casefold() not in " ".join(str(item) for item in header.get("to") or []).casefold():
        return False
    if filters.get("subject") and str(filters["subject"]).casefold() not in str(header.get("subject") or "").casefold():
        return False
    if filters.get("unread") is not None and bool(header.get("unread")) is not bool(filters["unread"]):
        return False
    date = str(header.get("date") or "")
    if filters.get("date_from") and date < str(filters["date_from"]):
        return False
    if filters.get("date_to") and date > str(filters["date_to"]):
        return False
    return bool(ref.get("mail_ref_id") or ref.get("uid") is not None)


def annotate_thread_counts(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    normalized = [normalize_message(row) for row in rows]
    for row in normalized:
        ref = dict(row.get("message_ref") or {})
        thread_ref = str(ref.get("thread_ref_id") or ref.get("mail_ref_id") or "")
        counts[thread_ref] = counts.get(thread_ref, 0) + 1
    for row in normalized:
        ref = dict(row.get("message_ref") or {})
        thread_ref = str(ref.get("thread_ref_id") or ref.get("mail_ref_id") or "")
        row["thread_message_count"] = counts.get(thread_ref, 1)
    return normalized


def account_status(account: Mapping[str, Any]) -> dict[str, Any]:
    enabled = bool(account.get("enabled", True))
    last_task = dict(account.get("last_task") or {})
    task_status = str(last_task.get("status") or "")
    if not enabled:
        state = "disabled"
        reason_code = "account_disabled"
    elif task_status in {"queued", "pending", "processing", "running"}:
        state = "syncing"
        reason_code = "mail_task_active"
    elif task_status in {"failed", "cancelled"}:
        state = "degraded"
        reason_code = str(last_task.get("reason_code") or f"mail_task_{task_status}")
    elif str(account.get("runtime_state") or "") == "offline":
        state = "offline"
        reason_code = "passive_provider_offline"
    else:
        state = "ready"
        reason_code = "passive_metadata_ready"
    return {
        **dict(account),
        "state": state,
        "reason_code": reason_code,
    }


def body_text(value: Mapping[str, Any]) -> str:
    for field in ("body_text", "text", "body", "value"):
        candidate = value.get(field)
        if isinstance(candidate, str):
            return candidate
        if isinstance(candidate, Mapping):
            nested = candidate.get("text") or candidate.get("value")
            if isinstance(nested, str):
                return nested
    return ""


def parse_search_filters(query: str) -> dict[str, Any]:
    filters: dict[str, Any] = {}
    for token in query.split():
        lowered = token.lower()
        if lowered.startswith("from:"):
            filters["from"] = token.split(":", 1)[1]
        elif lowered.startswith("to:"):
            filters["to"] = token.split(":", 1)[1]
        elif lowered.startswith("subject:"):
            filters["subject"] = token.split(":", 1)[1]
        elif lowered.startswith("mailbox:"):
            filters["mailbox"] = token.split(":", 1)[1]
        elif lowered.startswith("date:"):
            value = token.split(":", 1)[1]
            if ".." in value:
                filters["date_from"], filters["date_to"] = value.split("..", 1)
        elif lowered.startswith("unread:"):
            filters["unread"] = token.split(":", 1)[1].lower() in {"1", "true", "yes", "on"}
        else:
            filters["subject"] = f"{filters.get('subject', '')} {token}".strip()
    return filters


__all__ = [
    "account_status",
    "annotate_thread_counts",
    "body_text",
    "header_meta",
    "json_safe",
    "mail_message_key",
    "mailboxes",
    "matches_filters",
    "message_ref",
    "normalize_message",
    "parse_search_filters",
]
