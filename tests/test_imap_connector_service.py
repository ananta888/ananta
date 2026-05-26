from __future__ import annotations

from agent.services.imap_connector_service import ImapConnectorService, StaticImapClient


class _Factory:
    def __init__(self, *, mode: str = "ok") -> None:
        self._mode = mode

    def create(self) -> StaticImapClient:
        if self._mode == "timeout":
            return StaticImapClient(fail_on_connect="timeout")
        if self._mode == "login":
            return StaticImapClient(fail_on_connect="login")
        return StaticImapClient(
            mailboxes=["INBOX", "Archive"],
            headers={
                "INBOX": [
                    {"uid": 1, "subject": "A"},
                    {"uid": 2, "subject": "B"},
                ]
            },
            bodies={"INBOX": {1: "body A", 2: "body B"}},
        )


def _account() -> dict:
    return {
        "account_id": "acc-1",
        "display_name": "Work",
        "host": "imap.example.com",
        "port": 993,
        "username_ref": "user://acc-1",
        "credential_ref": "secret://imap/acc-1",
        "auth_mode": "password_app_token",
        "tls_mode": "require_tls",
        "sync_policy": "headers_only",
        "enabled": True,
    }


def test_imap_connector_lists_mailboxes_and_headers_and_body_on_explicit_request() -> None:
    service = ImapConnectorService(client_factory=_Factory())
    connected = service.connect_account(account=_account(), credential="app-token", username="alice")
    assert connected["ok"] is True
    session = connected["session"]

    mailboxes = service.list_mailboxes(session)
    assert mailboxes["ok"] is True
    assert mailboxes["mailboxes"] == ["INBOX", "Archive"]

    headers = service.fetch_headers(session, mailbox="INBOX", limit=1)
    assert headers["ok"] is True
    assert headers["headers"] == [{"uid": 1, "subject": "A"}]

    body = service.fetch_body(session, mailbox="INBOX", uid=1)
    assert body["ok"] is True
    assert body["body"] == "body A"


def test_imap_connector_returns_reason_code_for_timeout_and_login_errors() -> None:
    timeout_service = ImapConnectorService(client_factory=_Factory(mode="timeout"))
    login_service = ImapConnectorService(client_factory=_Factory(mode="login"))
    timeout = timeout_service.connect_account(account=_account(), credential="token", username="alice")
    login = login_service.connect_account(account=_account(), credential="token", username="alice")
    assert timeout["ok"] is False
    assert timeout["reason_code"] == "imap_connect_timeout"
    assert login["ok"] is False
    assert login["reason_code"] == "imap_login_failed"
