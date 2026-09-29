from __future__ import annotations

from agent.services.imap_threading_service import annotate_messages_with_thread_counts, group_mail_threads


def _row(message_id: str, *, in_reply_to: str = "", references: list[str] | None = None) -> dict:
    return {
        "message_ref": {
            "account_id": "acc-1",
            "mailbox": "INBOX",
            "uid": abs(hash(message_id)) % 9999 + 1,
            "message_id": message_id,
            "date": "2026-05-27T00:00:00Z",
            "from": "sender@example.com",
            "to": "team@example.com",
            "subject_hash": "s",
        },
        "header_meta": {
            "subject": "thread",
            "in_reply_to": in_reply_to,
            "references": list(references or []),
        },
    }


def test_thread_grouping_handles_simple_reply_chain() -> None:
    rows = [_row("<a@x>"), _row("<b@x>", in_reply_to="<a@x>"), _row("<c@x>", references=["<a@x>", "<b@x>"])]
    grouped = group_mail_threads(rows)
    assert len(grouped["threads"]) == 1
    assert grouped["threads"][0]["thread_count"] == 3


def test_thread_grouping_degrades_with_missing_parent() -> None:
    rows = [_row("<a@x>", in_reply_to="<missing@x>"), _row("<b@x>")]
    enriched = annotate_messages_with_thread_counts(rows)
    assert len(enriched) == 2
    assert all(int(item.get("thread_count") or 0) >= 1 for item in enriched)
