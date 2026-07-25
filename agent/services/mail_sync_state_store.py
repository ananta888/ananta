from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol

import portalocker

from agent.services.mail_contract_service import MailMessageMetadata, MailMessageRefV2
from agent.services.mail_provider_ports import (
    MailMessage,
    MailProviderResult,
    MailSyncCursor,
    MailSyncDelta,
)


@dataclass(frozen=True, slots=True)
class JmapSyncCheckpoint:
    account_id: str
    provider_account_id: str
    scope: str
    mailbox_state: str = ""
    email_state: str = ""
    query_state: str = ""
    query_fingerprint: str = ""
    revision: int = 0


class MailSyncStateStore(Protocol):
    def load(
        self,
        *,
        account_id: str,
        provider_account_id: str,
        scope: str,
        query_fingerprint: str,
    ) -> JmapSyncCheckpoint | None:
        ...

    def apply_and_commit(
        self,
        *,
        expected_revision: int,
        checkpoint: JmapSyncCheckpoint,
        delta: MailSyncDelta,
        replace_scope: bool = False,
    ) -> bool:
        """Atomically apply metadata delta and cursor or return False on conflict."""
        ...


class MailSyncDeltaApplier(Protocol):
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
        """Must be idempotent for the same transaction_id."""
        ...


class MailSyncStateStoreError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = str(reason_code or "mail_sync_state_store_failed")
        super().__init__(self.reason_code)


class PersistentMailSyncStateStore:
    _SCHEMA = "mail_sync_state.v1"

    def __init__(
        self,
        *,
        store_path: str | Path,
        delta_applier: MailSyncDeltaApplier,
        lock_timeout_seconds: float = 10.0,
        maximum_store_bytes: int = 64 * 1024 * 1024,
    ) -> None:
        self._path = Path(store_path).resolve()
        self._lock_path = self._path.with_suffix(f"{self._path.suffix}.lock")
        self._delta_applier = delta_applier
        self._lock_timeout_seconds = max(0.1, float(lock_timeout_seconds))
        self._maximum_store_bytes = max(1024, int(maximum_store_bytes))

    def load(
        self,
        *,
        account_id: str,
        provider_account_id: str,
        scope: str,
        query_fingerprint: str,
    ) -> JmapSyncCheckpoint | None:
        key = _scope_key(
            account_id=account_id,
            provider_account_id=provider_account_id,
            scope=scope,
            query_fingerprint=query_fingerprint,
        )
        with self._locked():
            payload = self._recover(self._load_payload())
            raw = dict(payload.get("entries") or {}).get(key)
            if raw is None:
                return None
            checkpoint = _checkpoint_from_dict(raw)
            if (
                checkpoint.account_id != account_id
                or checkpoint.provider_account_id != provider_account_id
                or checkpoint.scope != scope
                or checkpoint.query_fingerprint != query_fingerprint
            ):
                raise MailSyncStateStoreError("mail_sync_state_scope_collision")
            return checkpoint

    def apply_and_commit(
        self,
        *,
        expected_revision: int,
        checkpoint: JmapSyncCheckpoint,
        delta: MailSyncDelta,
        replace_scope: bool = False,
    ) -> bool:
        key = _scope_key(
            account_id=checkpoint.account_id,
            provider_account_id=checkpoint.provider_account_id,
            scope=checkpoint.scope,
            query_fingerprint=checkpoint.query_fingerprint,
        )
        with self._locked():
            payload = self._recover(self._load_payload())
            entries = dict(payload.get("entries") or {})
            current_raw = entries.get(key)
            current_revision = int(dict(current_raw or {}).get("revision") or 0)
            if current_revision != int(expected_revision):
                return False
            pending = {
                "transaction_id": _transaction_id(key, checkpoint.revision, delta),
                "scope_key": key,
                "checkpoint": _checkpoint_to_dict(checkpoint),
                "delta": _delta_to_dict(delta),
                "replace_scope": bool(replace_scope),
            }
            payload["pending"] = pending
            payload["entries"] = entries
            self._save_payload(payload)
            applied = self._apply_pending(pending)
            if not applied.ok:
                raise MailSyncStateStoreError(applied.reason_code)
            entries[key] = _checkpoint_to_dict(checkpoint)
            payload["entries"] = entries
            payload["pending"] = None
            self._save_payload(payload)
            return True

    def _recover(self, payload: dict[str, Any]) -> dict[str, Any]:
        raw_pending = payload.get("pending")
        if not isinstance(raw_pending, Mapping):
            return payload
        pending = dict(raw_pending)
        applied = self._apply_pending(pending)
        if not applied.ok:
            raise MailSyncStateStoreError(applied.reason_code)
        checkpoint = _checkpoint_from_dict(dict(pending.get("checkpoint") or {}))
        entries = dict(payload.get("entries") or {})
        current_revision = int(dict(entries.get(str(pending.get("scope_key"))) or {}).get("revision") or 0)
        if current_revision < checkpoint.revision:
            entries[str(pending["scope_key"])] = _checkpoint_to_dict(checkpoint)
        payload["entries"] = entries
        payload["pending"] = None
        self._save_payload(payload)
        return payload

    def _apply_pending(self, pending: Mapping[str, Any]) -> MailProviderResult[None]:
        checkpoint = _checkpoint_from_dict(dict(pending.get("checkpoint") or {}))
        delta = _delta_from_dict(dict(pending.get("delta") or {}))
        try:
            return self._delta_applier.apply(
                transaction_id=str(pending.get("transaction_id") or ""),
                account_id=checkpoint.account_id,
                provider_account_id=checkpoint.provider_account_id,
                scope=checkpoint.scope,
                delta=delta,
                replace_scope=bool(pending.get("replace_scope")),
            )
        except Exception as exc:
            raise MailSyncStateStoreError("mail_sync_delta_applier_unavailable") from exc

    def _load_payload(self) -> dict[str, Any]:
        if not self._path.exists():
            return {"schema": self._SCHEMA, "entries": {}, "pending": None}
        try:
            if self._path.stat().st_size > self._maximum_store_bytes:
                raise MailSyncStateStoreError("mail_sync_state_store_too_large")
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except MailSyncStateStoreError:
            raise
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise MailSyncStateStoreError("mail_sync_state_store_corrupt") from exc
        if not isinstance(raw, dict) or raw.get("schema") != self._SCHEMA or not isinstance(raw.get("entries"), dict):
            raise MailSyncStateStoreError("mail_sync_state_store_schema_invalid")
        return raw

    def _save_payload(self, payload: Mapping[str, Any]) -> None:
        encoded = (json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode(
            "utf-8"
        )
        if len(encoded) > self._maximum_store_bytes:
            raise MailSyncStateStoreError("mail_sync_state_store_too_large")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_name(f".{self._path.name}.{uuid.uuid4().hex}.tmp")
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self._path)
            os.chmod(self._path, 0o600)
            directory_fd = os.open(self._path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError as exc:
            raise MailSyncStateStoreError("mail_sync_state_store_write_failed") from exc
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def _locked(self) -> Any:
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        return portalocker.Lock(
            str(self._lock_path),
            mode="a+",
            timeout=self._lock_timeout_seconds,
            flags=portalocker.LOCK_EX | portalocker.LOCK_NB,
        )


def query_fingerprint(*, filters: Mapping[str, Any], sort: tuple[Mapping[str, Any], ...]) -> str:
    canonical = json.dumps(
        {"filter": dict(filters), "sort": [dict(item) for item in sort]},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _scope_key(
    *,
    account_id: str,
    provider_account_id: str,
    scope: str,
    query_fingerprint: str,
) -> str:
    values = (account_id, provider_account_id, scope, query_fingerprint)
    if any(not str(value).strip() for value in values):
        raise MailSyncStateStoreError("mail_sync_state_scope_invalid")
    canonical = "\x1f".join(str(value) for value in values)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _checkpoint_to_dict(value: JmapSyncCheckpoint) -> dict[str, Any]:
    return {
        "account_id": value.account_id,
        "provider_account_id": value.provider_account_id,
        "scope": value.scope,
        "mailbox_state": value.mailbox_state,
        "email_state": value.email_state,
        "query_state": value.query_state,
        "query_fingerprint": value.query_fingerprint,
        "revision": value.revision,
    }


def _checkpoint_from_dict(raw: Mapping[str, Any]) -> JmapSyncCheckpoint:
    try:
        checkpoint = JmapSyncCheckpoint(
            account_id=str(raw["account_id"]),
            provider_account_id=str(raw["provider_account_id"]),
            scope=str(raw["scope"]),
            mailbox_state=str(raw.get("mailbox_state") or ""),
            email_state=str(raw.get("email_state") or ""),
            query_state=str(raw.get("query_state") or ""),
            query_fingerprint=str(raw["query_fingerprint"]),
            revision=int(raw.get("revision") or 0),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise MailSyncStateStoreError("mail_sync_checkpoint_invalid") from exc
    if checkpoint.revision < 0:
        raise MailSyncStateStoreError("mail_sync_checkpoint_invalid")
    return checkpoint


def _cursor_to_dict(value: MailSyncCursor) -> dict[str, Any]:
    return {
        "account_id": value.account_id,
        "protocol": value.protocol,
        "scope": value.scope,
        "mailbox_state": value.mailbox_state,
        "email_state": value.email_state,
        "query_state": value.query_state,
    }


def _cursor_from_dict(raw: Mapping[str, Any]) -> MailSyncCursor:
    try:
        return MailSyncCursor(
            account_id=str(raw["account_id"]),
            protocol=str(raw["protocol"]),
            scope=str(raw.get("scope") or "default"),
            mailbox_state=str(raw.get("mailbox_state") or ""),
            email_state=str(raw.get("email_state") or ""),
            query_state=str(raw.get("query_state") or ""),
        )
    except KeyError as exc:
        raise MailSyncStateStoreError("mail_sync_cursor_invalid") from exc


def _message_to_dict(value: MailMessage) -> dict[str, Any]:
    return {
        "message_ref": value.message_ref.to_dict(),
        "metadata": value.metadata.to_dict(),
    }


def _message_from_dict(raw: Mapping[str, Any]) -> MailMessage:
    try:
        return MailMessage(
            message_ref=MailMessageRefV2.from_mapping(dict(raw["message_ref"])),
            metadata=MailMessageMetadata.from_mapping(dict(raw["metadata"])),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise MailSyncStateStoreError("mail_sync_message_invalid") from exc


def _delta_to_dict(value: MailSyncDelta) -> dict[str, Any]:
    return {
        "cursor": _cursor_to_dict(value.cursor),
        "created": [_message_to_dict(item) for item in value.created],
        "updated": [_message_to_dict(item) for item in value.updated],
        "destroyed_mail_ref_ids": list(value.destroyed_mail_ref_ids),
        "rebuild_required": value.rebuild_required,
    }


def _delta_from_dict(raw: Mapping[str, Any]) -> MailSyncDelta:
    try:
        created = tuple(_message_from_dict(item) for item in list(raw.get("created") or []))
        updated = tuple(_message_from_dict(item) for item in list(raw.get("updated") or []))
        destroyed = tuple(str(item) for item in list(raw.get("destroyed_mail_ref_ids") or []))
        return MailSyncDelta(
            cursor=_cursor_from_dict(dict(raw["cursor"])),
            created=created,
            updated=updated,
            destroyed_mail_ref_ids=destroyed,
            rebuild_required=bool(raw.get("rebuild_required")),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise MailSyncStateStoreError("mail_sync_delta_invalid") from exc


def _transaction_id(scope_key: str, revision: int, delta: MailSyncDelta) -> str:
    canonical = json.dumps(_delta_to_dict(delta), ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    material = f"{scope_key}|{int(revision)}|{canonical}"
    return f"mailsync-{hashlib.sha256(material.encode('utf-8')).hexdigest()}"


def sync_transaction_id(
    *,
    checkpoint: JmapSyncCheckpoint,
    delta: MailSyncDelta,
) -> str:
    scope_key = _scope_key(
        account_id=checkpoint.account_id,
        provider_account_id=checkpoint.provider_account_id,
        scope=checkpoint.scope,
        query_fingerprint=checkpoint.query_fingerprint,
    )
    return _transaction_id(scope_key, checkpoint.revision, delta)


__all__ = [
    "JmapSyncCheckpoint",
    "MailSyncDeltaApplier",
    "MailSyncStateStore",
    "MailSyncStateStoreError",
    "PersistentMailSyncStateStore",
    "query_fingerprint",
    "sync_transaction_id",
]
