"""SQLite-backed atomic metadata and mail-sync checkpoint composition.

This module is worker infrastructure: the backend owns the transaction
protocol, while the worker composition root supplies the concrete durable
adapter. Metadata, scope membership, cursor and checkpoint are committed in
one SQLite transaction.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Mapping

from agent.services.mail_contract_service import (
    MailMessageMetadata,
    MailMessageRefV2,
)
from agent.services.mail_metadata_store_service import (
    MailLocatorAlias,
    locator_alias_for_ref,
)
from agent.services.mail_provider_ports import (
    MailProviderResult,
    MailSyncCursor,
    MailSyncDelta,
    VerifiedMailContentAccess,
)
from agent.services.mail_sync_state_store import JmapSyncCheckpoint
from agent.services.mail_sync_transaction_service import (
    MailSyncTransactionRequest,
    TransactionalMailSyncStateStore,
)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _locator_key(
    *,
    account_id: str,
    protocol: str,
    locator: Mapping[str, Any],
    locator_version: int,
) -> str:
    canonical = _json(dict(locator))
    material = f"{account_id}|{protocol}|{int(locator_version)}|{canonical}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _checkpoint_key(checkpoint: JmapSyncCheckpoint) -> tuple[str, str, str, str]:
    return (
        checkpoint.account_id,
        checkpoint.provider_account_id,
        checkpoint.scope,
        checkpoint.query_fingerprint,
    )


def _require_access(
    access: VerifiedMailContentAccess,
    *,
    message_ref: MailMessageRefV2,
    allowed_scopes: set[str],
) -> None:
    if not isinstance(access, VerifiedMailContentAccess):
        raise PermissionError("verified_mail_content_access_required")
    if access.account_id != message_ref.account_id:
        raise PermissionError("mail_content_account_mismatch")
    if access.mail_ref_id != message_ref.mail_ref_id:
        raise PermissionError("mail_content_message_mismatch")
    if access.release_scope not in allowed_scopes:
        raise PermissionError("mail_content_scope_denied")
    if not access.artifact_ref.startswith(f"mail://{message_ref.mail_ref_id}"):
        raise PermissionError("mail_content_artifact_mismatch")


class SqliteMailMetadataStore:
    """Canonical worker metadata store shared by sync, body and mutation paths."""

    def __init__(
        self,
        *,
        database_path: str | Path,
        busy_timeout_seconds: float = 10.0,
    ) -> None:
        self._path = Path(database_path).resolve()
        self._busy_timeout_ms = int(
            max(1.0, min(float(busy_timeout_seconds), 60.0)) * 1000
        )
        self._path.parent.mkdir(parents=True, exist_ok=True)
        connection = self._connect()
        try:
            self._initialize(connection)
        finally:
            connection.close()
        os.chmod(self._path, 0o600)

    @property
    def database_path(self) -> Path:
        return self._path

    def connection(self) -> sqlite3.Connection:
        return self._connect()

    def upsert_message(
        self,
        *,
        message_ref: MailMessageRefV2,
        metadata: MailMessageMetadata,
    ) -> dict[str, Any]:
        return self._write(
            lambda connection: self._upsert_message(
                connection,
                message_ref=message_ref,
                metadata=metadata,
            )
        )

    def get_by_mail_ref_id(self, mail_ref_id: str) -> dict[str, Any] | None:
        connection = self._connect()
        try:
            row = connection.execute(
                """
                SELECT message_ref_json, metadata_json, stale, body_json,
                       body_scope, attachments_json, updated_at
                  FROM mail_messages
                 WHERE mail_ref_id = ?
                """,
                (str(mail_ref_id),),
            ).fetchone()
            return self._row_mapping(row) if row is not None else None
        finally:
            connection.close()

    def list_messages(self, *, account_id: str | None = None) -> list[dict[str, Any]]:
        connection = self._connect()
        try:
            if account_id is None:
                rows = connection.execute(
                    """
                    SELECT message_ref_json, metadata_json, stale, body_json,
                           body_scope, attachments_json, updated_at
                      FROM mail_messages
                     ORDER BY mail_ref_id
                    """
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT message_ref_json, metadata_json, stale, body_json,
                           body_scope, attachments_json, updated_at
                      FROM mail_messages
                     WHERE account_id = ?
                     ORDER BY mail_ref_id
                    """,
                    (str(account_id),),
                ).fetchall()
            return [self._row_mapping(row) for row in rows]
        finally:
            connection.close()

    def store_body(
        self,
        *,
        mail_ref_id: str,
        text_body: str,
        html_body: str,
        access: VerifiedMailContentAccess,
    ) -> dict[str, Any]:
        def update(connection: sqlite3.Connection) -> dict[str, Any]:
            row = self._message_row(connection, mail_ref_id)
            if row is None:
                raise ValueError("mail_message_not_found")
            message_ref = MailMessageRefV2.from_mapping(json.loads(row[0]))
            _require_access(
                access,
                message_ref=message_ref,
                allowed_scopes={"body_excerpt", "full_body"},
            )
            connection.execute(
                """
                UPDATE mail_messages
                   SET body_json = ?, body_scope = ?, updated_at = ?
                 WHERE mail_ref_id = ?
                """,
                (
                    _json({"text": str(text_body), "html": str(html_body)}),
                    access.release_scope,
                    _now_iso(),
                    str(mail_ref_id),
                ),
            )
            updated = self._message_row(connection, mail_ref_id)
            assert updated is not None
            return self._row_mapping(updated)

        return self._write(update)

    def store_attachments(
        self,
        *,
        mail_ref_id: str,
        attachments: list[Mapping[str, Any]],
        access: VerifiedMailContentAccess,
    ) -> dict[str, Any]:
        def update(connection: sqlite3.Connection) -> dict[str, Any]:
            row = self._message_row(connection, mail_ref_id)
            if row is None:
                raise ValueError("mail_message_not_found")
            message_ref = MailMessageRefV2.from_mapping(json.loads(row[0]))
            _require_access(
                access,
                message_ref=message_ref,
                allowed_scopes={"attachment_ref"},
            )
            connection.execute(
                """
                UPDATE mail_messages
                   SET attachments_json = ?, updated_at = ?
                 WHERE mail_ref_id = ?
                """,
                (
                    _json([dict(item) for item in attachments]),
                    _now_iso(),
                    str(mail_ref_id),
                ),
            )
            updated = self._message_row(connection, mail_ref_id)
            assert updated is not None
            return self._row_mapping(updated)

        return self._write(update)

    def mark_stale(self, *, mail_ref_id: str, stale: bool = True) -> dict[str, Any]:
        def update(connection: sqlite3.Connection) -> dict[str, Any]:
            changed = connection.execute(
                """
                UPDATE mail_messages
                   SET stale = ?, updated_at = ?
                 WHERE mail_ref_id = ?
                """,
                (int(bool(stale)), _now_iso(), str(mail_ref_id)),
            ).rowcount
            if not changed:
                raise ValueError("mail_message_not_found")
            row = self._message_row(connection, mail_ref_id)
            assert row is not None
            return self._row_mapping(row)

        return self._write(update)

    def delete_message(self, *, mail_ref_id: str) -> bool:
        return self._write(
            lambda connection: bool(
                connection.execute(
                    "DELETE FROM mail_messages WHERE mail_ref_id = ?",
                    (str(mail_ref_id),),
                ).rowcount
            )
        )

    def save_sync_cursor(self, cursor: MailSyncCursor) -> MailSyncCursor:
        def save(connection: sqlite3.Connection) -> MailSyncCursor:
            self._save_cursor(connection, cursor)
            return cursor

        return self._write(save)

    def get_sync_cursor(
        self,
        *,
        account_id: str,
        protocol: str,
        scope: str = "default",
    ) -> MailSyncCursor | None:
        connection = self._connect()
        try:
            row = connection.execute(
                """
                SELECT mailbox_state, email_state, query_state
                  FROM mail_sync_cursors
                 WHERE account_id = ? AND protocol = ? AND scope = ?
                """,
                (str(account_id), str(protocol), str(scope)),
            ).fetchone()
            if row is None:
                return None
            return MailSyncCursor(
                account_id=str(account_id),
                protocol=str(protocol),
                scope=str(scope),
                mailbox_state=str(row[0] or ""),
                email_state=str(row[1] or ""),
                query_state=str(row[2] or ""),
            )
        finally:
            connection.close()

    def list_locator_aliases(
        self,
        *,
        account_id: str | None = None,
        mail_ref_id: str | None = None,
    ) -> list[MailLocatorAlias]:
        clauses: list[str] = []
        values: list[str] = []
        if account_id is not None:
            clauses.append("account_id = ?")
            values.append(str(account_id))
        if mail_ref_id is not None:
            clauses.append("mail_ref_id = ?")
            values.append(str(mail_ref_id))
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT alias_id, mail_ref_id, account_id, protocol,
                       locator_json, locator_version, alias_version
                  FROM mail_locator_aliases
                """
                + where
                + " ORDER BY alias_id",
                values,
            ).fetchall()
            return [
                MailLocatorAlias(
                    alias_id=str(row[0]),
                    mail_ref_id=str(row[1]),
                    account_id=str(row[2]),
                    protocol=str(row[3]),
                    protocol_locator=dict(json.loads(row[4])),
                    locator_version=int(row[5]),
                    alias_version=int(row[6]),
                )
                for row in rows
            ]
        finally:
            connection.close()

    def resolve_locator(
        self,
        *,
        account_id: str,
        protocol: str,
        protocol_locator: Mapping[str, Any],
        locator_version: int,
    ) -> str | None:
        key = _locator_key(
            account_id=str(account_id),
            protocol=str(protocol),
            locator=protocol_locator,
            locator_version=int(locator_version),
        )
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT mail_ref_id FROM mail_locator_aliases WHERE locator_key = ?",
                (key,),
            ).fetchall()
            ids = {str(row[0]) for row in rows}
            if len(ids) > 1:
                raise ValueError("mail_locator_alias_ambiguous")
            return next(iter(ids), None)
        finally:
            connection.close()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self._path,
            timeout=self._busy_timeout_ms / 1000.0,
            isolation_level=None,
        )
        connection.execute(f"PRAGMA busy_timeout = {self._busy_timeout_ms}")
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = FULL")
        return connection

    @staticmethod
    def _initialize(connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS mail_messages (
                mail_ref_id TEXT PRIMARY KEY,
                account_id TEXT NOT NULL,
                protocol TEXT NOT NULL,
                message_ref_json TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                stale INTEGER NOT NULL DEFAULT 0,
                body_json TEXT NOT NULL DEFAULT '{}',
                body_scope TEXT NOT NULL DEFAULT 'metadata_only',
                attachments_json TEXT NOT NULL DEFAULT '[]',
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS mail_messages_account
                ON mail_messages(account_id);
            CREATE TABLE IF NOT EXISTS mail_scope_messages (
                account_id TEXT NOT NULL,
                provider_account_id TEXT NOT NULL,
                scope TEXT NOT NULL,
                query_fingerprint TEXT NOT NULL,
                mail_ref_id TEXT NOT NULL,
                PRIMARY KEY (
                    account_id,
                    provider_account_id,
                    scope,
                    query_fingerprint,
                    mail_ref_id
                ),
                FOREIGN KEY(mail_ref_id)
                    REFERENCES mail_messages(mail_ref_id)
                    ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS mail_locator_aliases (
                alias_id TEXT PRIMARY KEY,
                locator_key TEXT NOT NULL UNIQUE,
                mail_ref_id TEXT NOT NULL,
                account_id TEXT NOT NULL,
                protocol TEXT NOT NULL,
                locator_json TEXT NOT NULL,
                locator_version INTEGER NOT NULL,
                alias_version INTEGER NOT NULL,
                FOREIGN KEY(mail_ref_id)
                    REFERENCES mail_messages(mail_ref_id)
                    ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS mail_sync_cursors (
                account_id TEXT NOT NULL,
                protocol TEXT NOT NULL,
                scope TEXT NOT NULL,
                mailbox_state TEXT NOT NULL,
                email_state TEXT NOT NULL,
                query_state TEXT NOT NULL,
                PRIMARY KEY(account_id, protocol, scope)
            );
            CREATE TABLE IF NOT EXISTS mail_sync_checkpoints (
                account_id TEXT NOT NULL,
                provider_account_id TEXT NOT NULL,
                scope TEXT NOT NULL,
                query_fingerprint TEXT NOT NULL,
                mailbox_state TEXT NOT NULL,
                email_state TEXT NOT NULL,
                query_state TEXT NOT NULL,
                revision INTEGER NOT NULL,
                PRIMARY KEY(
                    account_id,
                    provider_account_id,
                    scope,
                    query_fingerprint
                )
            );
            """
        )

    def _write(self, operation: Callable[[sqlite3.Connection], Any]) -> Any:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            result = operation(connection)
            connection.commit()
            return result
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _upsert_message(
        self,
        connection: sqlite3.Connection,
        *,
        message_ref: MailMessageRefV2,
        metadata: MailMessageMetadata,
    ) -> dict[str, Any]:
        now = _now_iso()
        connection.execute(
            """
            INSERT INTO mail_messages(
                mail_ref_id, account_id, protocol, message_ref_json,
                metadata_json, stale, body_json, body_scope,
                attachments_json, updated_at
            ) VALUES (?, ?, ?, ?, ?, 0, '{}', 'metadata_only', '[]', ?)
            ON CONFLICT(mail_ref_id) DO UPDATE SET
                account_id = excluded.account_id,
                protocol = excluded.protocol,
                message_ref_json = excluded.message_ref_json,
                metadata_json = excluded.metadata_json,
                stale = 0,
                updated_at = excluded.updated_at
            """,
            (
                message_ref.mail_ref_id,
                message_ref.account_id,
                message_ref.protocol,
                _json(message_ref.to_dict()),
                _json(metadata.to_dict()),
                now,
            ),
        )
        alias = locator_alias_for_ref(message_ref)
        key = _locator_key(
            account_id=message_ref.account_id,
            protocol=message_ref.protocol,
            locator=message_ref.protocol_locator,
            locator_version=message_ref.locator_version,
        )
        collision = connection.execute(
            "SELECT mail_ref_id FROM mail_locator_aliases WHERE locator_key = ?",
            (key,),
        ).fetchone()
        if collision is not None and str(collision[0]) != message_ref.mail_ref_id:
            raise ValueError("mail_locator_alias_conflict")
        connection.execute(
            """
            INSERT INTO mail_locator_aliases(
                alias_id, locator_key, mail_ref_id, account_id, protocol,
                locator_json, locator_version, alias_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(locator_key) DO UPDATE SET
                alias_id = excluded.alias_id,
                mail_ref_id = excluded.mail_ref_id,
                account_id = excluded.account_id,
                protocol = excluded.protocol,
                locator_json = excluded.locator_json,
                locator_version = excluded.locator_version,
                alias_version = excluded.alias_version
            """,
            (
                alias.alias_id,
                key,
                alias.mail_ref_id,
                alias.account_id,
                alias.protocol,
                _json(dict(alias.protocol_locator)),
                alias.locator_version,
                alias.alias_version,
            ),
        )
        row = self._message_row(connection, message_ref.mail_ref_id)
        assert row is not None
        return self._row_mapping(row)

    @staticmethod
    def _save_cursor(connection: sqlite3.Connection, cursor: MailSyncCursor) -> None:
        connection.execute(
            """
            INSERT INTO mail_sync_cursors(
                account_id, protocol, scope, mailbox_state,
                email_state, query_state
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(account_id, protocol, scope) DO UPDATE SET
                mailbox_state = excluded.mailbox_state,
                email_state = excluded.email_state,
                query_state = excluded.query_state
            """,
            (
                cursor.account_id,
                cursor.protocol,
                cursor.scope,
                cursor.mailbox_state,
                cursor.email_state,
                cursor.query_state,
            ),
        )

    @staticmethod
    def _message_row(
        connection: sqlite3.Connection,
        mail_ref_id: str,
    ) -> sqlite3.Row | tuple[Any, ...] | None:
        return connection.execute(
            """
            SELECT message_ref_json, metadata_json, stale, body_json,
                   body_scope, attachments_json, updated_at
              FROM mail_messages
             WHERE mail_ref_id = ?
            """,
            (str(mail_ref_id),),
        ).fetchone()

    @staticmethod
    def _row_mapping(row: sqlite3.Row | tuple[Any, ...]) -> dict[str, Any]:
        return {
            "message_ref": dict(json.loads(row[0])),
            "metadata": dict(json.loads(row[1])),
            "stale": bool(row[2]),
            "body": dict(json.loads(row[3])),
            "body_scope": str(row[4]),
            "attachments": list(json.loads(row[5])),
            "updated_at": str(row[6]),
        }


class SqliteMailSyncTransactionPort:
    """Concrete rollback-capable implementation of MailSyncTransactionPort."""

    def __init__(
        self,
        *,
        metadata_store: SqliteMailMetadataStore,
        fault_injector: Callable[[str], None] | None = None,
    ) -> None:
        self.metadata_store = metadata_store
        self._fault_injector = fault_injector

    def load_checkpoint(
        self,
        *,
        account_id: str,
        provider_account_id: str,
        scope: str,
        query_fingerprint: str,
    ) -> MailProviderResult[JmapSyncCheckpoint | None]:
        connection = self.metadata_store.connection()
        try:
            row = connection.execute(
                """
                SELECT mailbox_state, email_state, query_state, revision
                  FROM mail_sync_checkpoints
                 WHERE account_id = ?
                   AND provider_account_id = ?
                   AND scope = ?
                   AND query_fingerprint = ?
                """,
                (
                    str(account_id),
                    str(provider_account_id),
                    str(scope),
                    str(query_fingerprint),
                ),
            ).fetchone()
            if row is None:
                return MailProviderResult.success(None, reason_code="ok")
            return MailProviderResult.success(
                JmapSyncCheckpoint(
                    account_id=str(account_id),
                    provider_account_id=str(provider_account_id),
                    scope=str(scope),
                    mailbox_state=str(row[0] or ""),
                    email_state=str(row[1] or ""),
                    query_state=str(row[2] or ""),
                    query_fingerprint=str(query_fingerprint),
                    revision=int(row[3]),
                ),
                reason_code="ok",
            )
        except sqlite3.Error:
            return MailProviderResult.failure("mail_sync_transaction_unavailable")
        finally:
            connection.close()

    def begin(
        self,
        request: MailSyncTransactionRequest,
    ) -> MailProviderResult["_SqliteMailSyncUnitOfWork"]:
        if self._fault_injector is not None:
            try:
                self._fault_injector("before_begin")
            except Exception:
                return MailProviderResult.failure(
                    "mail_sync_transaction_unavailable"
                )
        connection = self.metadata_store.connection()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT revision
                  FROM mail_sync_checkpoints
                 WHERE account_id = ?
                   AND provider_account_id = ?
                   AND scope = ?
                   AND query_fingerprint = ?
                """,
                _checkpoint_key(request.checkpoint),
            ).fetchone()
            current_revision = int(row[0]) if row is not None else 0
            if current_revision != request.expected_revision:
                connection.rollback()
                connection.close()
                return MailProviderResult.failure("mail_sync_concurrent_update")
            return MailProviderResult.success(
                _SqliteMailSyncUnitOfWork(
                    connection=connection,
                    metadata_store=self.metadata_store,
                    request=request,
                    fault_injector=self._fault_injector,
                ),
                reason_code="ok",
            )
        except sqlite3.Error:
            connection.rollback()
            connection.close()
            return MailProviderResult.failure("mail_sync_transaction_unavailable")


class _SqliteMailSyncUnitOfWork:
    def __init__(
        self,
        *,
        connection: sqlite3.Connection,
        metadata_store: SqliteMailMetadataStore,
        request: MailSyncTransactionRequest,
        fault_injector: Callable[[str], None] | None,
    ) -> None:
        self._connection = connection
        self._metadata_store = metadata_store
        self._request = request
        self._fault_injector = fault_injector
        self._metadata_applied = False
        self._checkpoint_staged: JmapSyncCheckpoint | None = None
        self._active = True

    def apply_metadata(
        self,
        *,
        delta: MailSyncDelta,
        replace_scope: bool,
    ) -> MailProviderResult[None]:
        if not self._active:
            return MailProviderResult.failure("mail_sync_transaction_closed")
        if delta != self._request.delta or bool(replace_scope) != bool(
            self._request.replace_scope
        ):
            return MailProviderResult.failure("mail_sync_transaction_delta_mismatch")
        checkpoint = self._request.checkpoint
        scope_key = _checkpoint_key(checkpoint)
        try:
            if replace_scope:
                self._connection.execute(
                    """
                    DELETE FROM mail_scope_messages
                     WHERE account_id = ?
                       AND provider_account_id = ?
                       AND scope = ?
                       AND query_fingerprint = ?
                    """,
                    scope_key,
                )
            for message in (*delta.created, *delta.updated):
                if message.message_ref.account_id != checkpoint.account_id:
                    raise ValueError("mail_sync_message_account_mismatch")
                self._metadata_store._upsert_message(
                    self._connection,
                    message_ref=message.message_ref,
                    metadata=message.metadata,
                )
                self._connection.execute(
                    """
                    INSERT OR IGNORE INTO mail_scope_messages(
                        account_id, provider_account_id, scope,
                        query_fingerprint, mail_ref_id
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (*scope_key, message.message_ref.mail_ref_id),
                )
            for mail_ref_id in delta.destroyed_mail_ref_ids:
                self._connection.execute(
                    """
                    DELETE FROM mail_scope_messages
                     WHERE account_id = ?
                       AND provider_account_id = ?
                       AND scope = ?
                       AND query_fingerprint = ?
                       AND mail_ref_id = ?
                    """,
                    (*scope_key, str(mail_ref_id)),
                )
            self._metadata_store._save_cursor(self._connection, delta.cursor)
            self._delete_orphan_messages()
            if self._fault_injector is not None:
                self._fault_injector("after_metadata")
            self._metadata_applied = True
            return MailProviderResult.success(None, reason_code="ok")
        except Exception:
            return MailProviderResult.failure("mail_metadata_apply_failed")

    def stage_checkpoint(
        self,
        *,
        checkpoint: JmapSyncCheckpoint,
    ) -> MailProviderResult[None]:
        if not self._active:
            return MailProviderResult.failure("mail_sync_transaction_closed")
        if checkpoint != self._request.checkpoint:
            return MailProviderResult.failure("mail_sync_checkpoint_mismatch")
        self._checkpoint_staged = checkpoint
        return MailProviderResult.success(None, reason_code="ok")

    def commit(self) -> MailProviderResult[None]:
        if not self._active:
            return MailProviderResult.failure("mail_sync_transaction_closed")
        if not self._metadata_applied or self._checkpoint_staged is None:
            return MailProviderResult.failure("mail_sync_transaction_incomplete")
        checkpoint = self._checkpoint_staged
        try:
            self._connection.execute(
                """
                INSERT INTO mail_sync_checkpoints(
                    account_id, provider_account_id, scope,
                    query_fingerprint, mailbox_state, email_state,
                    query_state, revision
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(
                    account_id, provider_account_id, scope, query_fingerprint
                ) DO UPDATE SET
                    mailbox_state = excluded.mailbox_state,
                    email_state = excluded.email_state,
                    query_state = excluded.query_state,
                    revision = excluded.revision
                """,
                (
                    checkpoint.account_id,
                    checkpoint.provider_account_id,
                    checkpoint.scope,
                    checkpoint.query_fingerprint,
                    checkpoint.mailbox_state,
                    checkpoint.email_state,
                    checkpoint.query_state,
                    checkpoint.revision,
                ),
            )
            if self._fault_injector is not None:
                self._fault_injector("before_commit")
            self._connection.commit()
            self._active = False
            self._connection.close()
            return MailProviderResult.success(None, reason_code="ok")
        except Exception:
            self.rollback()
            return MailProviderResult.failure("mail_sync_transaction_commit_failed")

    def rollback(self) -> None:
        if not self._active:
            return
        try:
            self._connection.rollback()
        finally:
            self._active = False
            self._connection.close()

    def _delete_orphan_messages(self) -> None:
        self._connection.execute(
            """
            DELETE FROM mail_messages
             WHERE mail_ref_id NOT IN (
                 SELECT DISTINCT mail_ref_id FROM mail_scope_messages
             )
            """
        )


@dataclass(frozen=True, slots=True)
class TransactionalMailRuntimeState:
    metadata_store: SqliteMailMetadataStore
    sync_state_store: TransactionalMailSyncStateStore
    transaction_port: SqliteMailSyncTransactionPort


def build_transactional_mail_runtime_state(
    *,
    database_path: str | Path,
    fault_injector: Callable[[str], None] | None = None,
) -> TransactionalMailRuntimeState:
    metadata = SqliteMailMetadataStore(database_path=database_path)
    port = SqliteMailSyncTransactionPort(
        metadata_store=metadata,
        fault_injector=fault_injector,
    )
    return TransactionalMailRuntimeState(
        metadata_store=metadata,
        sync_state_store=TransactionalMailSyncStateStore(transaction_port=port),
        transaction_port=port,
    )


__all__ = [
    "SqliteMailMetadataStore",
    "SqliteMailSyncTransactionPort",
    "TransactionalMailRuntimeState",
    "build_transactional_mail_runtime_state",
]
