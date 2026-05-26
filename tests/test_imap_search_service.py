from __future__ import annotations

from agent.services.imap_metadata_store_service import ImapMetadataStore
from agent.services.imap_search_service import search_mail_metadata


def _message_ref(uid: int, *, mailbox: str = "INBOX", sender: str = "sender@example.com", to: str = "team@example.com") -> dict:
    return {
        "account_id": "acc-1",
        "mailbox": mailbox,
        "uid": uid,
        "message_id": f"<m{uid}@example.com>",
        "date": "2026-05-27T00:00:00Z",
        "from": sender,
        "to": to,
        "subject_hash": f"subj-{uid}",
    }


def test_mail_search_filters_header_metadata(tmp_path) -> None:
    store = ImapMetadataStore(store_path=tmp_path / "mail-meta.json")
    store.upsert_message(message_ref=_message_ref(1, sender="alice@example.com"), header_meta={"subject": "Build failed", "unread": True, "starred": False})
    store.upsert_message(message_ref=_message_ref(2, sender="bob@example.com"), header_meta={"subject": "Status update", "unread": False, "starred": True})
    result = search_mail_metadata(
        store=store,
        filters={"from": "alice@", "subject": "build", "mailbox": "INBOX", "unread": True},
    )
    assert result["count"] == 1
    assert result["results"][0]["source_ref"].endswith("/1")
    assert result["results"][0]["policy_state"] == "metadata_only"


def test_mail_search_does_not_search_body_by_default(tmp_path) -> None:
    store = ImapMetadataStore(store_path=tmp_path / "mail-meta.json")
    store.upsert_message(message_ref=_message_ref(3), header_meta={"subject": "Weekly"})
    store.store_body(account_id="acc-1", mailbox="INBOX", uid=3, body="contains hidden token", release_scope="body_excerpt")
    disabled = search_mail_metadata(store=store, filters={}, body_contains="hidden", include_body_search=False)
    enabled = search_mail_metadata(store=store, filters={}, body_contains="hidden", include_body_search=True)
    assert disabled["count"] == 0
    assert enabled["count"] == 1
