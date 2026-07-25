from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence

import portalocker

from agent.services.mail_metadata_store_service import MailMetadataStore
from agent.services.mail_provider_ports import (
    MailMailbox,
    MailProviderResult,
    MailSyncDelta,
)


class MailMetadataSyncDeltaApplier:
    """Idempotently projects a provider sync delta into the metadata store."""

    def __init__(self, *, metadata_store: MailMetadataStore) -> None:
        self._metadata = metadata_store

    def apply(
        self,
        *,
        transaction_id: str,
        account_id: str,
        provider_account_id: str,
        scope: str,
        delta: MailSyncDelta,
        replace_scope: bool,
    ) -> MailProviderResult[None]:
        if not all(
            str(value or "").strip()
            for value in (transaction_id, account_id, provider_account_id, scope)
        ):
            return MailProviderResult.failure("mail_sync_delta_context_invalid")
        if delta.cursor.account_id != account_id:
            return MailProviderResult.failure("mail_sync_delta_account_mismatch")
        try:
            incoming = {
                message.message_ref.mail_ref_id
                for message in (*delta.created, *delta.updated)
            }
            if replace_scope:
                for row in self._metadata.list_messages(account_id=account_id):
                    raw_ref = row.get("message_ref")
                    if not isinstance(raw_ref, Mapping):
                        continue
                    locator = raw_ref.get("protocol_locator")
                    provider_id = (
                        str(locator.get("provider_account_id") or "")
                        if isinstance(locator, Mapping)
                        else ""
                    )
                    same_provider = (
                        not provider_id
                        or provider_id == str(provider_account_id)
                    )
                    mail_ref_id = str(raw_ref.get("mail_ref_id") or "")
                    if same_provider and mail_ref_id and mail_ref_id not in incoming:
                        self._metadata.delete_message(mail_ref_id=mail_ref_id)
            for mail_ref_id in delta.destroyed_mail_ref_ids:
                self._metadata.delete_message(mail_ref_id=str(mail_ref_id))
            for message in (*delta.created, *delta.updated):
                self._metadata.upsert_message(
                    message_ref=message.message_ref,
                    metadata=message.metadata,
                )
            self._metadata.save_sync_cursor(delta.cursor)
        except (OSError, TypeError, ValueError):
            return MailProviderResult.failure(
                "mail_sync_metadata_projection_failed",
                retryable=True,
            )
        return MailProviderResult.success(
            reason_code="mail_sync_metadata_projected"
        )


class PersistentMailboxLocatorStore:
    """Stores opaque mailbox references and their provider locators."""

    _SCHEMA = "ananta.mailbox-locators.v1"

    def __init__(
        self,
        *,
        store_path: str | Path,
        lock_timeout_seconds: float = 10.0,
        maximum_store_bytes: int = 8 * 1024 * 1024,
    ) -> None:
        self._path = Path(store_path).resolve()
        self._lock_path = self._path.with_suffix(f"{self._path.suffix}.lock")
        self._lock_timeout = max(0.1, float(lock_timeout_seconds))
        self._maximum_store_bytes = max(1024, int(maximum_store_bytes))

    def remember(
        self,
        *,
        account_id: str,
        mailboxes: Sequence[MailMailbox],
    ) -> MailProviderResult[None]:
        account = str(account_id or "").strip()
        if not account:
            return MailProviderResult.failure("mailbox_locator_account_required")
        rows: dict[str, dict[str, str]] = {}
        for mailbox in mailboxes:
            locator = dict(mailbox.provider_locator)
            provider_id = str(
                locator.get("mailbox_id") or locator.get("mailbox") or ""
            ).strip()
            mailbox_ref_id = str(mailbox.mailbox_ref_id or "").strip()
            if not provider_id or not mailbox_ref_id:
                return MailProviderResult.failure("mailbox_locator_invalid")
            rows[mailbox_ref_id] = {
                "provider_id": provider_id,
                "role": str(mailbox.role or "").strip().lower(),
            }
        try:
            with self._locked():
                payload = self._load()
                accounts = dict(payload.get("accounts") or {})
                accounts[account] = rows
                payload["accounts"] = accounts
                self._save(payload)
        except (OSError, ValueError, portalocker.exceptions.LockException):
            return MailProviderResult.failure(
                "mailbox_locator_store_unavailable",
                retryable=True,
            )
        return MailProviderResult.success(reason_code="mailbox_locators_stored")

    def resolve_mailbox(
        self,
        *,
        account_id: str,
        mailbox_ref_id: str,
    ) -> MailProviderResult[str]:
        row = self._resolve(account_id=account_id, mailbox_ref_id=mailbox_ref_id)
        if row is None:
            return MailProviderResult.failure("mailbox_locator_not_found")
        return MailProviderResult.success(str(row["provider_id"]))

    def resolve_role(
        self,
        *,
        account_id: str,
        role: str,
    ) -> MailProviderResult[str]:
        target = str(role or "").strip().lower()
        if not target:
            return MailProviderResult.failure("mailbox_role_required")
        try:
            with self._locked():
                rows = dict(
                    dict(self._load().get("accounts") or {}).get(
                        str(account_id), {}
                    )
                )
        except (OSError, ValueError, portalocker.exceptions.LockException):
            return MailProviderResult.failure(
                "mailbox_locator_store_unavailable",
                retryable=True,
            )
        for row in rows.values():
            if (
                isinstance(row, Mapping)
                and str(row.get("role") or "").lower() == target
                and str(row.get("provider_id") or "")
            ):
                return MailProviderResult.success(str(row["provider_id"]))
        return MailProviderResult.failure("mailbox_role_not_found")

    def _resolve(
        self,
        *,
        account_id: str,
        mailbox_ref_id: str,
    ) -> Mapping[str, str] | None:
        try:
            with self._locked():
                accounts = dict(self._load().get("accounts") or {})
                rows = accounts.get(str(account_id))
                if not isinstance(rows, Mapping):
                    return None
                row = rows.get(str(mailbox_ref_id))
                return dict(row) if isinstance(row, Mapping) else None
        except (OSError, ValueError, portalocker.exceptions.LockException):
            return None

    def _load(self) -> dict[str, Any]:
        if not self._path.exists():
            return {"schema": self._SCHEMA, "accounts": {}}
        if self._path.stat().st_size > self._maximum_store_bytes:
            raise ValueError("mailbox_locator_store_too_large")
        payload = json.loads(self._path.read_text(encoding="utf-8"))
        if (
            not isinstance(payload, dict)
            or payload.get("schema") != self._SCHEMA
            or not isinstance(payload.get("accounts"), dict)
        ):
            raise ValueError("mailbox_locator_store_invalid")
        return payload

    def _save(self, payload: Mapping[str, Any]) -> None:
        encoded = (
            json.dumps(
                dict(payload),
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        if len(encoded) > self._maximum_store_bytes:
            raise ValueError("mailbox_locator_store_too_large")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self._path.name}.",
            suffix=".tmp",
            dir=self._path.parent,
        )
        temporary = Path(temporary_name)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self._path)
            os.chmod(self._path, 0o600)
        finally:
            temporary.unlink(missing_ok=True)

    def _locked(self) -> Any:
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        return portalocker.Lock(
            str(self._lock_path),
            mode="a+",
            timeout=self._lock_timeout,
            flags=portalocker.LOCK_EX | portalocker.LOCK_NB,
        )


__all__ = [
    "MailMetadataSyncDeltaApplier",
    "PersistentMailboxLocatorStore",
]
