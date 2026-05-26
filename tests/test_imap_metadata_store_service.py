from __future__ import annotations

from agent.services.imap_metadata_store_service import ImapMetadataStore


def _message_ref(uid: int = 1) -> dict:
    return {
        "account_id": "acc-1",
        "mailbox": "INBOX",
        "uid": uid,
        "message_id": f"<m{uid}@example.com>",
        "date": "2026-05-27T00:00:00Z",
        "from": "sender@example.com",
        "to": "team@example.com",
        "subject_hash": f"subj-{uid}",
    }


def test_metadata_store_create_update_and_lookup(tmp_path) -> None:
    store = ImapMetadataStore(store_path=tmp_path / "mail-meta.json")
    inserted = store.upsert_message(message_ref=_message_ref(1), header_meta={"subject": "A", "unread": True})
    assert inserted["message_ref"]["uid"] == 1
    by_uid = store.get_by_uid(account_id="acc-1", mailbox="INBOX", uid=1)
    assert by_uid is not None
    by_mid = store.get_by_message_id(message_id="<m1@example.com>")
    assert by_mid is not None

    updated = store.upsert_message(message_ref=_message_ref(1), header_meta={"subject": "A2", "unread": False})
    assert updated["header_meta"]["subject"] == "A2"
    assert store.get_by_uid(account_id="acc-1", mailbox="INBOX", uid=1)["header_meta"]["subject"] == "A2"


def test_metadata_store_body_requires_explicit_release_scope(tmp_path) -> None:
    store = ImapMetadataStore(store_path=tmp_path / "mail-meta.json")
    store.upsert_message(message_ref=_message_ref(1), header_meta={"subject": "A"})
    denied = store.store_body(
        account_id="acc-1",
        mailbox="INBOX",
        uid=1,
        body="private body",
        release_scope="metadata_only",
    )
    assert denied["ok"] is False
    assert denied["reason_code"] == "policy_scope_denied"
    allowed = store.store_body(
        account_id="acc-1",
        mailbox="INBOX",
        uid=1,
        body="private body",
        release_scope="body_excerpt",
    )
    assert allowed["ok"] is True
    assert store.get_by_uid(account_id="acc-1", mailbox="INBOX", uid=1)["body_scope"] == "body_excerpt"


def test_metadata_store_can_mark_messages_stale(tmp_path) -> None:
    store = ImapMetadataStore(store_path=tmp_path / "mail-meta.json")
    store.upsert_message(message_ref=_message_ref(2), header_meta={"subject": "B"})
    stale = store.mark_stale(account_id="acc-1", mailbox="INBOX", uid=2, stale=True)
    assert stale["stale"] is True
