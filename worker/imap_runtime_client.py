"""Secure production IMAP client behind the legacy IMAP provider port."""

from __future__ import annotations

import email.policy
import imaplib
import re
import socket
import ssl
from email.header import decode_header, make_header
from email.parser import BytesParser
from typing import Any, Callable

from agent.services.jmap_endpoint_policy import JmapEndpointPolicy
from agent.services.mail_contract_service import subject_hash

_HOST = re.compile(r"^[A-Za-z0-9.-]{1,253}$")
_SIZE = re.compile(rb"RFC822\\.SIZE\\s+(\\d+)", re.IGNORECASE)


def _header(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except (LookupError, UnicodeDecodeError):
        return str(value)


class SecureImapClient:
    def __init__(
        self,
        *,
        endpoint_policy: JmapEndpointPolicy,
        connect_timeout_seconds: float = 10.0,
        maximum_header_bytes: int = 256 * 1024,
        maximum_body_bytes: int = 8 * 1024 * 1024,
        ssl_context: ssl.SSLContext | None = None,
        imap_ssl_factory: Callable[..., Any] = imaplib.IMAP4_SSL,
    ) -> None:
        self._endpoint_policy = endpoint_policy
        self._connect_timeout = max(1.0, min(float(connect_timeout_seconds), 30.0))
        self._maximum_header_bytes = max(1024, int(maximum_header_bytes))
        self._maximum_body_bytes = max(1024, int(maximum_body_bytes))
        self._ssl_context = ssl_context or self._default_ssl_context()
        self._imap_ssl_factory = imap_ssl_factory
        self._connection: Any = None

    @staticmethod
    def _default_ssl_context() -> ssl.SSLContext:
        context = ssl.create_default_context()
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.check_hostname = True
        context.verify_mode = ssl.CERT_REQUIRED
        return context

    def connect(
        self,
        *,
        host: str,
        port: int,
        username: str,
        credential: str,
        use_tls: bool,
    ) -> None:
        clean_host = str(host or "").strip().lower()
        clean_username = str(username or "")
        clean_credential = str(credential or "")
        clean_port = int(port)
        if (
            not use_tls
            or not _HOST.fullmatch(clean_host)
            or clean_port < 1
            or clean_port > 65535
            or not clean_username
            or not clean_credential
        ):
            raise ValueError("imap_connect_invalid_config")
        try:
            self._endpoint_policy.validate_initial(
                f"https://{clean_host}:{clean_port}/",
                purpose="session",
            )
            connection = self._imap_ssl_factory(
                clean_host,
                clean_port,
                ssl_context=self._ssl_context,
                timeout=self._connect_timeout,
            )
            status, _ = connection.login(clean_username, clean_credential)
        except (socket.timeout, TimeoutError) as exc:
            raise TimeoutError("imap_connect_timeout") from exc
        except imaplib.IMAP4.error as exc:
            raise ValueError("imap_login_failed") from exc
        if str(status).upper() != "OK":
            try:
                connection.logout()
            except Exception:
                pass
            raise ValueError("imap_login_failed")
        self._connection = connection

    def list_mailboxes(self) -> list[str]:
        connection = self._require_connection()
        status, rows = connection.list()
        if str(status).upper() != "OK":
            raise ConnectionError("imap_list_failed")
        result: list[str] = []
        for raw in rows or []:
            value = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
            name = value.rsplit(" ", 1)[-1].strip().strip('"')
            if name:
                result.append(name)
        return result

    def fetch_headers(self, *, mailbox: str, limit: int) -> list[dict[str, Any]]:
        connection = self._select(mailbox)
        status, search_rows = connection.uid("search", None, "ALL")
        if str(status).upper() != "OK" or not search_rows:
            raise ConnectionError("imap_search_failed")
        raw_ids = search_rows[0] if isinstance(search_rows[0], bytes) else b""
        uid_values = raw_ids.split()[-max(1, int(limit)) :]
        uid_validity = self._uid_validity(connection)
        rows: list[dict[str, Any]] = []
        for raw_uid in reversed(uid_values):
            status, fetched = connection.uid(
                "fetch",
                raw_uid,
                "(UID RFC822.SIZE BODY.PEEK[HEADER.FIELDS "
                "(MESSAGE-ID DATE FROM TO SUBJECT)])",
            )
            if str(status).upper() != "OK":
                raise ConnectionError("imap_fetch_headers_failed")
            metadata, header_bytes = self._fetch_parts(fetched)
            if len(header_bytes) > self._maximum_header_bytes:
                raise ValueError("imap_header_too_large")
            message = BytesParser(policy=email.policy.default).parsebytes(
                header_bytes,
                headersonly=True,
            )
            subject = _header(message.get("Subject"))
            size_match = _SIZE.search(metadata)
            rows.append(
                {
                    "uid": int(raw_uid),
                    "uidvalidity": uid_validity,
                    "message_id": _header(message.get("Message-ID")),
                    "date": _header(message.get("Date")),
                    "from": _header(message.get("From")),
                    "to": _header(message.get("To")),
                    "subject": subject,
                    "subject_hash": subject_hash(subject),
                    "size": int(size_match.group(1)) if size_match else None,
                }
            )
        return rows

    def fetch_body(self, *, mailbox: str, uid: int) -> str:
        connection = self._select(mailbox)
        status, fetched = connection.uid("fetch", str(int(uid)), "(BODY.PEEK[])")
        if str(status).upper() != "OK":
            raise ValueError("imap_body_not_found")
        _, raw = self._fetch_parts(fetched)
        if not raw:
            raise ValueError("imap_body_not_found")
        if len(raw) > self._maximum_body_bytes:
            raise ValueError("imap_body_too_large")
        message = BytesParser(policy=email.policy.default).parsebytes(raw)
        output: list[str] = []
        parts = message.walk() if message.is_multipart() else (message,)
        for part in parts:
            if part.is_multipart() or part.get_content_disposition() == "attachment":
                continue
            if part.get_content_type() != "text/plain":
                continue
            try:
                output.append(str(part.get_content()))
            except (LookupError, UnicodeDecodeError):
                payload = part.get_payload(decode=True) or b""
                output.append(payload.decode("utf-8", errors="replace"))
        return "\n".join(output)

    def disconnect(self) -> None:
        connection, self._connection = self._connection, None
        if connection is None:
            return
        try:
            connection.logout()
        except (imaplib.IMAP4.error, OSError):
            pass

    def _require_connection(self) -> Any:
        if self._connection is None:
            raise ConnectionError("imap_not_connected")
        return self._connection

    def _select(self, mailbox: str) -> Any:
        connection = self._require_connection()
        status, _ = connection.select(str(mailbox), readonly=True)
        if str(status).upper() != "OK":
            raise ConnectionError("imap_mailbox_select_failed")
        return connection

    @staticmethod
    def _uid_validity(connection: Any) -> int | None:
        try:
            _, values = connection.response("UIDVALIDITY")
            if values and values[0]:
                return int(values[0])
        except (TypeError, ValueError, imaplib.IMAP4.error):
            return None
        return None

    @staticmethod
    def _fetch_parts(rows: Any) -> tuple[bytes, bytes]:
        metadata = b""
        content = b""
        for row in rows or []:
            if not isinstance(row, tuple) or len(row) < 2:
                continue
            if isinstance(row[0], bytes):
                metadata += row[0]
            if isinstance(row[1], bytes):
                content += row[1]
        return metadata, content


class SecureImapClientFactory:
    def __init__(
        self,
        *,
        endpoint_policy: JmapEndpointPolicy,
        connect_timeout_seconds: float = 10.0,
        maximum_header_bytes: int = 256 * 1024,
        maximum_body_bytes: int = 8 * 1024 * 1024,
    ) -> None:
        self._endpoint_policy = endpoint_policy
        self._connect_timeout = connect_timeout_seconds
        self._maximum_header_bytes = maximum_header_bytes
        self._maximum_body_bytes = maximum_body_bytes

    def create(self) -> SecureImapClient:
        return SecureImapClient(
            endpoint_policy=self._endpoint_policy,
            connect_timeout_seconds=self._connect_timeout,
            maximum_header_bytes=self._maximum_header_bytes,
            maximum_body_bytes=self._maximum_body_bytes,
        )


__all__ = ["SecureImapClient", "SecureImapClientFactory"]
