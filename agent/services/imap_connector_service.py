from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from agent.services.imap_contract_service import validate_imap_account_config


class ImapClient(Protocol):
    def connect(self, *, host: str, port: int, username: str, credential: str, use_tls: bool) -> None:
        ...

    def list_mailboxes(self) -> list[str]:
        ...

    def fetch_headers(self, *, mailbox: str, limit: int) -> list[dict[str, Any]]:
        ...

    def fetch_body(self, *, mailbox: str, uid: int) -> str:
        ...

    def disconnect(self) -> None:
        ...


class ImapClientFactory(Protocol):
    def create(self) -> ImapClient:
        ...


class StaticImapClient:
    def __init__(
        self,
        *,
        mailboxes: list[str] | None = None,
        headers: dict[str, list[dict[str, Any]]] | None = None,
        bodies: dict[str, dict[int, str]] | None = None,
        fail_on_connect: str = "",
    ) -> None:
        self._mailboxes = list(mailboxes or ["INBOX"])
        self._headers = {str(k): [dict(item) for item in list(v)] for k, v in dict(headers or {}).items()}
        self._bodies = {str(k): {int(uid): str(text) for uid, text in dict(v).items()} for k, v in dict(bodies or {}).items()}
        self._connected = False
        self._fail_on_connect = str(fail_on_connect or "").strip()

    def connect(self, *, host: str, port: int, username: str, credential: str, use_tls: bool) -> None:
        if self._fail_on_connect == "timeout":
            raise TimeoutError("imap_connect_timeout")
        if self._fail_on_connect == "login":
            raise ValueError("imap_login_failed")
        if not use_tls:
            raise ValueError("imap_tls_required")
        if not str(host).strip() or int(port) <= 0 or not str(username).strip() or not str(credential).strip():
            raise ValueError("imap_connect_invalid_config")
        self._connected = True

    def list_mailboxes(self) -> list[str]:
        if not self._connected:
            raise ConnectionError("imap_not_connected")
        return list(self._mailboxes)

    def fetch_headers(self, *, mailbox: str, limit: int) -> list[dict[str, Any]]:
        if not self._connected:
            raise ConnectionError("imap_not_connected")
        rows = [dict(item) for item in list(self._headers.get(str(mailbox), []))]
        return rows[: max(1, int(limit))]

    def fetch_body(self, *, mailbox: str, uid: int) -> str:
        if not self._connected:
            raise ConnectionError("imap_not_connected")
        body = dict(self._bodies.get(str(mailbox), {})).get(int(uid))
        if body is None:
            raise ValueError("imap_body_not_found")
        return str(body)

    def disconnect(self) -> None:
        self._connected = False


@dataclass(frozen=True)
class ImapSession:
    account_id: str
    client: ImapClient


class ImapConnectorService:
    def __init__(self, *, client_factory: ImapClientFactory) -> None:
        self._client_factory = client_factory

    def connect_account(
        self,
        *,
        account: dict[str, Any],
        credential: str,
        username: str,
    ) -> dict[str, Any]:
        issues = validate_imap_account_config(account)
        if issues:
            return {"ok": False, "reason_code": issues[0]["reason_code"], "session": None}
        try:
            client = self._client_factory.create()
            client.connect(
                host=str(account.get("host") or ""),
                port=int(account.get("port") or 0),
                username=str(username or ""),
                credential=str(credential or ""),
                use_tls=str(account.get("tls_mode") or "") == "require_tls",
            )
        except TimeoutError:
            return {"ok": False, "reason_code": "imap_connect_timeout", "session": None}
        except ValueError as exc:
            return {"ok": False, "reason_code": str(exc) or "imap_connect_failed", "session": None}
        except (ConnectionError, OSError, RuntimeError):
            return {"ok": False, "reason_code": "imap_connect_failed", "session": None}
        session = ImapSession(account_id=str(account.get("account_id") or ""), client=client)
        return {"ok": True, "reason_code": "connected", "session": session}

    def list_mailboxes(self, session: ImapSession) -> dict[str, Any]:
        try:
            return {"ok": True, "reason_code": "ok", "mailboxes": list(session.client.list_mailboxes())}
        except (ConnectionError, RuntimeError, OSError):
            return {"ok": False, "reason_code": "imap_not_connected", "mailboxes": []}

    def fetch_headers(self, session: ImapSession, *, mailbox: str, limit: int = 50) -> dict[str, Any]:
        try:
            headers = session.client.fetch_headers(mailbox=str(mailbox), limit=max(1, int(limit)))
            return {"ok": True, "reason_code": "ok", "headers": headers}
        except (ConnectionError, RuntimeError, OSError):
            return {"ok": False, "reason_code": "imap_not_connected", "headers": []}

    def fetch_body(self, session: ImapSession, *, mailbox: str, uid: int) -> dict[str, Any]:
        try:
            body = session.client.fetch_body(mailbox=str(mailbox), uid=int(uid))
            return {"ok": True, "reason_code": "ok", "body": body}
        except ValueError as exc:
            return {"ok": False, "reason_code": str(exc) or "imap_body_not_found", "body": ""}
        except (ConnectionError, RuntimeError, OSError):
            return {"ok": False, "reason_code": "imap_not_connected", "body": ""}

    def disconnect(self, session: ImapSession) -> dict[str, Any]:
        try:
            session.client.disconnect()
        except (ConnectionError, RuntimeError, OSError):
            return {"ok": False, "reason_code": "imap_disconnect_failed"}
        return {"ok": True, "reason_code": "disconnected"}
