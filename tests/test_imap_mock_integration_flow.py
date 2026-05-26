from __future__ import annotations

from pathlib import Path
from typing import Any

from agent.services.imap_connector_service import ImapConnectorService, StaticImapClient
from agent.services.imap_metadata_store_service import ImapMetadataStore
from agent.services.imap_sync_policy_service import build_imap_sync_plan


class _RecordingImapClient(StaticImapClient):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.fetch_body_calls: list[tuple[str, int]] = []

    def fetch_body(self, *, mailbox: str, uid: int) -> str:
        self.fetch_body_calls.append((str(mailbox), int(uid)))
        return super().fetch_body(mailbox=mailbox, uid=uid)


class _Factory:
    def __init__(self, client: StaticImapClient) -> None:
        self._client = client

    def create(self) -> StaticImapClient:
        return self._client


def _account(*, tls_mode: str = "require_tls", sync_policy: str = "headers_only") -> dict[str, Any]:
    return {
        "account_id": "acc-imap-1",
        "display_name": "Work",
        "host": "imap.example.com",
        "port": 993,
        "username_ref": "user://alice",
        "credential_ref": "secret://imap/alice",
        "auth_mode": "password_app_token",
        "tls_mode": tls_mode,
        "sync_policy": sync_policy,
        "enabled": True,
    }


def _sync_headers_from_mock(
    *,
    service: ImapConnectorService,
    account: dict[str, Any],
    store: ImapMetadataStore,
    with_body: bool,
) -> tuple[dict[str, Any], _RecordingImapClient]:
    connected = service.connect_account(account=account, credential="token", username="alice")
    assert connected["ok"] is True
    session = connected["session"]
    mailboxes = service.list_mailboxes(session)
    assert mailboxes["ok"] is True
    plan = build_imap_sync_plan(
        sync_policy=str(account.get("sync_policy") or "headers_only"),
        total_available=20,
        requested_limit=20,
    )
    for mailbox in list(mailboxes["mailboxes"]):
        fetched = service.fetch_headers(session, mailbox=str(mailbox), limit=int(plan["header_limit"]))
        assert fetched["ok"] is True
        for header in list(fetched["headers"]):
            uid = int(header["uid"])
            message_ref = {
                "account_id": str(account["account_id"]),
                "mailbox": str(mailbox),
                "uid": uid,
                "message_id": str(header.get("message_id") or f"<m{uid}@example.com>"),
                "date": str(header.get("date") or "2026-05-27T00:00:00Z"),
                "from": str(header.get("from") or "alerts@example.com"),
                "to": str(header.get("to") or "team@example.com"),
                "subject_hash": str(header.get("subject_hash") or f"s{uid}"),
            }
            store.upsert_message(
                message_ref=message_ref,
                header_meta={
                    "subject": str(header.get("subject") or ""),
                    "unread": bool(header.get("unread", False)),
                    "starred": bool(header.get("starred", False)),
                },
            )
            attachments = [dict(item) for item in list(header.get("attachments") or []) if isinstance(item, dict)]
            if attachments:
                stored = store.store_attachments(
                    account_id=message_ref["account_id"],
                    mailbox=message_ref["mailbox"],
                    uid=uid,
                    attachments=attachments,
                )
                assert stored["ok"] is True
            if with_body and bool(plan["include_body"]):
                body = service.fetch_body(session, mailbox=str(mailbox), uid=uid)
                assert body["ok"] is True
                stored_body = store.store_body(
                    account_id=message_ref["account_id"],
                    mailbox=message_ref["mailbox"],
                    uid=uid,
                    body=str(body["body"]),
                    release_scope="body_excerpt",
                )
                assert stored_body["ok"] is True
    return connected, session.client


def test_mock_imap_integration_provides_mailbox_headers_body_and_attachment_metadata(tmp_path: Path) -> None:
    client = _RecordingImapClient(
        mailboxes=["INBOX", "Archive"],
        headers={
            "INBOX": [
                {
                    "uid": 10,
                    "subject": "CI Alert",
                    "message_id": "<m10@example.com>",
                    "date": "2026-05-27T00:00:00Z",
                    "from": "alerts@example.com",
                    "to": "team@example.com",
                    "attachments": [{"filename": "trace.log", "content_type": "text/plain", "size": 128}],
                }
            ]
        },
        bodies={"INBOX": {10: "build failed token=abc123"}},
    )
    service = ImapConnectorService(client_factory=_Factory(client))
    store = ImapMetadataStore(store_path=tmp_path / "data" / "imap" / "mail-metadata.json")
    connected, used_client = _sync_headers_from_mock(
        service=service,
        account=_account(sync_policy="headers_only"),
        store=store,
        with_body=False,
    )
    assert connected["ok"] is True
    row = store.get_by_uid(account_id="acc-imap-1", mailbox="INBOX", uid=10)
    assert row is not None
    assert row["header_meta"]["subject"] == "CI Alert"
    assert row["attachments"][0]["filename"] == "trace.log"
    explicit_body = service.fetch_body(connected["session"], mailbox="INBOX", uid=10)
    assert explicit_body["ok"] is True
    assert "token=abc123" in explicit_body["body"]
    assert len(used_client.fetch_body_calls) == 1


def test_mock_imap_integration_reports_timeout_login_and_tls_required_errors() -> None:
    timeout_service = ImapConnectorService(client_factory=_Factory(_RecordingImapClient(fail_on_connect="timeout")))
    login_service = ImapConnectorService(client_factory=_Factory(_RecordingImapClient(fail_on_connect="login")))
    timeout = timeout_service.connect_account(account=_account(), credential="token", username="alice")
    login = login_service.connect_account(account=_account(), credential="token", username="alice")
    tls_required = login_service.connect_account(account=_account(tls_mode="none"), credential="token", username="alice")
    assert timeout["ok"] is False
    assert timeout["reason_code"] == "imap_connect_timeout"
    assert login["ok"] is False
    assert login["reason_code"] == "imap_login_failed"
    assert tls_required["ok"] is False
    assert tls_required["reason_code"] == "tls_mode_must_require_tls"


def test_headers_only_sync_keeps_body_unsynced_until_explicit_request(tmp_path: Path) -> None:
    client = _RecordingImapClient(
        mailboxes=["INBOX"],
        headers={"INBOX": [{"uid": 3, "subject": "Only headers", "message_id": "<m3@example.com>"}]},
        bodies={"INBOX": {3: "body-3"}},
    )
    service = ImapConnectorService(client_factory=_Factory(client))
    store = ImapMetadataStore(store_path=tmp_path / "data" / "imap" / "mail-metadata.json")
    connected, used_client = _sync_headers_from_mock(
        service=service,
        account=_account(sync_policy="headers_only"),
        store=store,
        with_body=True,
    )
    assert connected["ok"] is True
    row = store.get_by_uid(account_id="acc-imap-1", mailbox="INBOX", uid=3)
    assert row is not None
    assert str(row.get("body") or "") == ""
    assert used_client.fetch_body_calls == []
