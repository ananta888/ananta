from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Mapping, TypeVar

from agent.services.mail_contract_service import (
    MAIL_METADATA_STORE_SCHEMA,
    MailMessageMetadata,
    MailMessageRefV2,
)
from agent.services.mail_provider_ports import MailSyncCursor, VerifiedMailContentAccess
from agent.services.mail_migration_journal import MailFileLock

R = TypeVar("R")


def _locked(method: Callable[..., R]) -> Callable[..., R]:
    @wraps(method)
    def wrapped(self: MailMetadataStore, *args: Any, **kwargs: Any) -> R:
        with MailFileLock(path=self._lock_path):
            return method(self, *args, **kwargs)

    return wrapped


@dataclass(frozen=True, slots=True)
class MailLocatorAlias:
    alias_id: str
    mail_ref_id: str
    account_id: str
    protocol: str
    protocol_locator: Mapping[str, Any]
    locator_version: int
    alias_version: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "alias_id": self.alias_id,
            "mail_ref_id": self.mail_ref_id,
            "account_id": self.account_id,
            "protocol": self.protocol,
            "protocol_locator": dict(self.protocol_locator),
            "locator_version": self.locator_version,
            "alias_version": self.alias_version,
        }


def _locator_key(*, account_id: str, protocol: str, locator: Mapping[str, Any], locator_version: int) -> str:
    import hashlib

    canonical = json.dumps(dict(locator), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(f"{account_id}|{protocol}|{locator_version}|{canonical}".encode("utf-8")).hexdigest()


def locator_alias_for_ref(message_ref: MailMessageRefV2, *, alias_version: int = 1) -> MailLocatorAlias:
    key = _locator_key(
        account_id=message_ref.account_id,
        protocol=message_ref.protocol,
        locator=message_ref.protocol_locator,
        locator_version=message_ref.locator_version,
    )
    return MailLocatorAlias(
        alias_id=f"mailalias-{key[:24]}-v{int(alias_version)}",
        mail_ref_id=message_ref.mail_ref_id,
        account_id=message_ref.account_id,
        protocol=message_ref.protocol,
        protocol_locator=message_ref.protocol_locator,
        locator_version=message_ref.locator_version,
        alias_version=int(alias_version),
    )


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _atomic_write(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(dict(payload), handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def _require_access(
    access: VerifiedMailContentAccess,
    *,
    account_id: str,
    mail_ref_id: str,
    allowed_scopes: set[str],
) -> None:
    if not isinstance(access, VerifiedMailContentAccess):
        raise PermissionError("verified_mail_content_access_required")
    if access.account_id != account_id:
        raise PermissionError("mail_content_account_mismatch")
    if access.mail_ref_id != mail_ref_id:
        raise PermissionError("mail_content_message_mismatch")
    if access.release_scope not in allowed_scopes:
        raise PermissionError("mail_content_scope_denied")
    if mail_ref_id not in access.artifact_ref:
        raise PermissionError("mail_content_artifact_mismatch")


class MailMetadataStore:
    def __init__(self, *, store_path: str | Path) -> None:
        self._path = Path(store_path).resolve()
        self._lock_path = self._path.with_suffix(f"{self._path.suffix}.lock")

    def _load(self) -> dict[str, Any]:
        if not self._path.exists():
            return {"schema": MAIL_METADATA_STORE_SCHEMA, "messages": [], "sync_cursors": [], "locator_aliases": []}
        payload = json.loads(self._path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("schema") != MAIL_METADATA_STORE_SCHEMA:
            raise ValueError("mail_metadata_store_schema_unsupported")
        payload.setdefault("messages", [])
        payload.setdefault("sync_cursors", [])
        payload.setdefault("locator_aliases", [])
        return payload

    def _save(self, payload: Mapping[str, Any]) -> None:
        _atomic_write(self._path, payload)

    @_locked
    def upsert_message(
        self,
        *,
        message_ref: MailMessageRefV2,
        metadata: MailMessageMetadata,
    ) -> dict[str, Any]:
        payload = self._load()
        rows = [dict(item) for item in payload["messages"] if isinstance(item, dict)]
        existing = next(
            (row for row in rows if str(dict(row.get("message_ref") or {}).get("mail_ref_id") or "") == message_ref.mail_ref_id),
            None,
        )
        row = {
            "message_ref": message_ref.to_dict(),
            "metadata": metadata.to_dict(),
            "stale": False,
            "body": dict(existing.get("body") or {}) if existing else {},
            "body_scope": str(existing.get("body_scope") or "metadata_only") if existing else "metadata_only",
            "attachments": list(existing.get("attachments") or []) if existing else [],
            "updated_at": _now_iso(),
        }
        rows = [
            item
            for item in rows
            if str(dict(item.get("message_ref") or {}).get("mail_ref_id") or "") != message_ref.mail_ref_id
        ]
        rows.append(row)
        payload["messages"] = rows
        aliases = [dict(item) for item in payload["locator_aliases"] if isinstance(item, dict)]
        alias = locator_alias_for_ref(message_ref)
        target_key = _locator_key(
            account_id=alias.account_id,
            protocol=alias.protocol,
            locator=alias.protocol_locator,
            locator_version=alias.locator_version,
        )
        collisions = [
            item
            for item in aliases
            if _locator_key(
                account_id=str(item.get("account_id") or ""),
                protocol=str(item.get("protocol") or ""),
                locator=dict(item.get("protocol_locator") or {}),
                locator_version=int(item.get("locator_version") or 1),
            )
            == target_key
        ]
        if any(str(item.get("mail_ref_id") or "") != alias.mail_ref_id for item in collisions):
            raise ValueError("mail_locator_alias_conflict")
        if not any(str(item.get("mail_ref_id") or "") == alias.mail_ref_id for item in collisions):
            next_version = max([int(item.get("alias_version") or 0) for item in aliases] or [0]) + 1
            aliases.append(locator_alias_for_ref(message_ref, alias_version=next_version).to_dict())
        payload["locator_aliases"] = aliases
        self._save(payload)
        return dict(row)

    @_locked
    def get_by_mail_ref_id(self, mail_ref_id: str) -> dict[str, Any] | None:
        target = str(mail_ref_id)
        for row in self._load()["messages"]:
            if not isinstance(row, dict):
                continue
            if str(dict(row.get("message_ref") or {}).get("mail_ref_id") or "") == target:
                return dict(row)
        return None

    @_locked
    def list_messages(self, *, account_id: str | None = None) -> list[dict[str, Any]]:
        rows = [dict(item) for item in self._load()["messages"] if isinstance(item, dict)]
        if account_id is None:
            return rows
        return [
            row
            for row in rows
            if str(dict(row.get("message_ref") or {}).get("account_id") or "") == str(account_id)
        ]

    @_locked
    def store_body(
        self,
        *,
        mail_ref_id: str,
        text_body: str,
        html_body: str,
        access: VerifiedMailContentAccess,
    ) -> dict[str, Any]:
        payload = self._load()
        rows = [dict(item) for item in payload["messages"] if isinstance(item, dict)]
        for index, row in enumerate(rows):
            ref = dict(row.get("message_ref") or {})
            if str(ref.get("mail_ref_id") or "") != str(mail_ref_id):
                continue
            _require_access(
                access,
                account_id=str(ref.get("account_id") or ""),
                mail_ref_id=str(mail_ref_id),
                allowed_scopes={"body_excerpt", "full_body"},
            )
            updated = dict(row)
            updated["body"] = {"text": str(text_body), "html": str(html_body)}
            updated["body_scope"] = access.release_scope
            updated["updated_at"] = _now_iso()
            rows[index] = updated
            payload["messages"] = rows
            self._save(payload)
            return updated
        raise ValueError("mail_message_not_found")

    @_locked
    def store_attachments(
        self,
        *,
        mail_ref_id: str,
        attachments: list[Mapping[str, Any]],
        access: VerifiedMailContentAccess,
    ) -> dict[str, Any]:
        payload = self._load()
        rows = [dict(item) for item in payload["messages"] if isinstance(item, dict)]
        for index, row in enumerate(rows):
            ref = dict(row.get("message_ref") or {})
            if str(ref.get("mail_ref_id") or "") != str(mail_ref_id):
                continue
            _require_access(
                access,
                account_id=str(ref.get("account_id") or ""),
                mail_ref_id=str(mail_ref_id),
                allowed_scopes={"attachment_ref"},
            )
            updated = dict(row)
            updated["attachments"] = [dict(item) for item in attachments]
            updated["updated_at"] = _now_iso()
            rows[index] = updated
            payload["messages"] = rows
            self._save(payload)
            return updated
        raise ValueError("mail_message_not_found")

    @_locked
    def mark_stale(self, *, mail_ref_id: str, stale: bool = True) -> dict[str, Any]:
        payload = self._load()
        rows = [dict(item) for item in payload["messages"] if isinstance(item, dict)]
        for index, row in enumerate(rows):
            if str(dict(row.get("message_ref") or {}).get("mail_ref_id") or "") == str(mail_ref_id):
                updated = dict(row)
                updated["stale"] = bool(stale)
                updated["updated_at"] = _now_iso()
                rows[index] = updated
                payload["messages"] = rows
                self._save(payload)
                return updated
        raise ValueError("mail_message_not_found")

    @_locked
    def delete_message(self, *, mail_ref_id: str) -> bool:
        payload = self._load()
        rows = [dict(item) for item in payload["messages"] if isinstance(item, dict)]
        kept = [
            row
            for row in rows
            if str(dict(row.get("message_ref") or {}).get("mail_ref_id") or "") != str(mail_ref_id)
        ]
        if len(kept) == len(rows):
            return False
        payload["messages"] = kept
        self._save(payload)
        return True

    @_locked
    def save_sync_cursor(self, cursor: MailSyncCursor) -> MailSyncCursor:
        payload = self._load()
        cursors = [dict(item) for item in payload["sync_cursors"] if isinstance(item, dict)]
        key = (cursor.account_id, cursor.protocol, cursor.scope)
        cursors = [
            item
            for item in cursors
            if (str(item.get("account_id")), str(item.get("protocol")), str(item.get("scope") or "default")) != key
        ]
        cursors.append(
            {
                "account_id": cursor.account_id,
                "protocol": cursor.protocol,
                "scope": cursor.scope,
                "mailbox_state": cursor.mailbox_state,
                "email_state": cursor.email_state,
                "query_state": cursor.query_state,
            }
        )
        payload["sync_cursors"] = cursors
        self._save(payload)
        return cursor

    @_locked
    def get_sync_cursor(self, *, account_id: str, protocol: str, scope: str = "default") -> MailSyncCursor | None:
        key = (str(account_id), str(protocol), str(scope))
        for item in self._load()["sync_cursors"]:
            if not isinstance(item, dict):
                continue
            if (str(item.get("account_id")), str(item.get("protocol")), str(item.get("scope") or "default")) == key:
                return MailSyncCursor(
                    account_id=key[0],
                    protocol=key[1],
                    scope=key[2],
                    mailbox_state=str(item.get("mailbox_state") or ""),
                    email_state=str(item.get("email_state") or ""),
                    query_state=str(item.get("query_state") or ""),
                )
        return None

    @_locked
    def list_locator_aliases(
        self,
        *,
        account_id: str | None = None,
        mail_ref_id: str | None = None,
    ) -> list[MailLocatorAlias]:
        aliases = [
            MailLocatorAlias(
                alias_id=str(item.get("alias_id") or ""),
                mail_ref_id=str(item.get("mail_ref_id") or ""),
                account_id=str(item.get("account_id") or ""),
                protocol=str(item.get("protocol") or ""),
                protocol_locator=dict(item.get("protocol_locator") or {}),
                locator_version=int(item.get("locator_version") or 1),
                alias_version=int(item.get("alias_version") or 1),
            )
            for item in self._load()["locator_aliases"]
            if isinstance(item, dict)
        ]
        if account_id is not None:
            aliases = [item for item in aliases if item.account_id == str(account_id)]
        if mail_ref_id is not None:
            aliases = [item for item in aliases if item.mail_ref_id == str(mail_ref_id)]
        return aliases

    @_locked
    def resolve_locator(
        self,
        *,
        account_id: str,
        protocol: str,
        protocol_locator: Mapping[str, Any],
        locator_version: int,
    ) -> str | None:
        target = _locator_key(
            account_id=str(account_id),
            protocol=str(protocol),
            locator=protocol_locator,
            locator_version=int(locator_version),
        )
        matches = [
            item
            for item in self._load()["locator_aliases"]
            if isinstance(item, dict)
            and _locator_key(
                account_id=str(item.get("account_id") or ""),
                protocol=str(item.get("protocol") or ""),
                locator=dict(item.get("protocol_locator") or {}),
                locator_version=int(item.get("locator_version") or 1),
            )
            == target
        ]
        ids = {str(item.get("mail_ref_id") or "") for item in matches}
        if len(ids) > 1:
            raise ValueError("mail_locator_alias_ambiguous")
        return next(iter(ids), None)
