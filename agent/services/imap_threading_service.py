from __future__ import annotations

from typing import Any


def _message_id(row: dict[str, Any]) -> str:
    ref = dict(row.get("message_ref") or {})
    return str(ref.get("message_id") or "").strip()


def _reply_target(row: dict[str, Any]) -> str:
    headers = dict(row.get("header_meta") or {})
    in_reply_to = str(headers.get("in_reply_to") or "").strip()
    if in_reply_to:
        return in_reply_to
    refs = [str(item).strip() for item in list(headers.get("references") or []) if str(item).strip()]
    return refs[-1] if refs else ""


def group_mail_threads(rows: list[dict[str, Any]]) -> dict[str, Any]:
    messages = [dict(item) for item in list(rows or []) if isinstance(item, dict)]
    by_id = {_message_id(item): item for item in messages if _message_id(item)}
    thread_of: dict[str, str] = {}

    def resolve_root(mid: str) -> str:
        current = mid
        visited: set[str] = set()
        while current and current not in visited:
            visited.add(current)
            row = by_id.get(current)
            if row is None:
                break
            parent = _reply_target(row)
            if not parent or parent not in by_id:
                break
            current = parent
        return current or mid

    for mid in by_id:
        root = resolve_root(mid)
        thread_of[mid] = root

    grouped: dict[str, list[str]] = {}
    for mid, root in thread_of.items():
        grouped.setdefault(root, []).append(mid)
    threads = [
        {"thread_id": root, "message_ids": sorted(ids), "thread_count": len(ids)}
        for root, ids in sorted(grouped.items(), key=lambda item: item[0])
    ]
    return {"threads": threads, "message_to_thread": thread_of}


def annotate_messages_with_thread_counts(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    messages = [dict(item) for item in list(rows or []) if isinstance(item, dict)]
    grouped = group_mail_threads(messages)
    counts: dict[str, int] = {}
    for thread in list(grouped.get("threads") or []):
        if not isinstance(thread, dict):
            continue
        thread_count = int(thread.get("thread_count") or 1)
        for mid in list(thread.get("message_ids") or []):
            counts[str(mid)] = thread_count
    enriched: list[dict[str, Any]] = []
    for row in messages:
        mid = _message_id(row)
        item = dict(row)
        item["thread_count"] = counts.get(mid, 1)
        item["thread_id"] = str(grouped.get("message_to_thread", {}).get(mid) or mid)
        enriched.append(item)
    return enriched
