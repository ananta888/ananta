"""Secret-reference key custody and tenant-scoped tamper-evident access audit."""

from __future__ import annotations

import hashlib
import hmac
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ananta_contracts.collaboration_workspace import canonical_digest, require_id


class CollaborationKeyCustody:
    """Stores key metadata only; key bytes are resolved from an external secret provider."""

    def __init__(
        self,
        path: str | Path,
        *,
        secret_resolver: Callable[[str], bytes],
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._resolve_secret = secret_resolver
        self._clock = clock
        with sqlite3.connect(self._path) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS collaboration_keys(
                    tenant_id TEXT,workspace_id TEXT,actor_binding_id TEXT,purpose TEXT,version INTEGER,
                    key_ref TEXT,status TEXT,created_at REAL,revoked_at REAL,
                    PRIMARY KEY(tenant_id,workspace_id,actor_binding_id,purpose,version)
                );
                CREATE UNIQUE INDEX IF NOT EXISTS collaboration_key_active
                ON collaboration_keys(tenant_id,workspace_id,actor_binding_id,purpose) WHERE status='active';
                CREATE TABLE IF NOT EXISTS collaboration_key_audit(
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,tenant_id TEXT,workspace_id TEXT,
                    actor_binding_id TEXT,purpose TEXT,version INTEGER,operation TEXT,occurred_at REAL,
                    previous_digest TEXT,entry_digest TEXT
                );
                """
            )

    def rotate(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        actor_binding_id: str,
        purpose: str,
        key_ref: str,
    ) -> dict[str, Any]:
        keys = self._keys(tenant_id, workspace_id, actor_binding_id, purpose)
        reference = require_id(key_ref, "key_ref")
        secret = self._resolve_secret(reference)
        if not isinstance(secret, bytes) or len(secret) < 32:
            raise ValueError("collaboration_key_material_invalid")
        del secret
        now = self._clock()
        with sqlite3.connect(self._path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT COALESCE(MAX(version),0) FROM collaboration_keys WHERE tenant_id=? AND workspace_id=? "
                "AND actor_binding_id=? AND purpose=?",
                keys,
            ).fetchone()
            version = int(current[0]) + 1
            connection.execute(
                "UPDATE collaboration_keys SET status='revoked',revoked_at=? WHERE tenant_id=? AND workspace_id=? "
                "AND actor_binding_id=? AND purpose=? AND status='active'",
                (now, *keys),
            )
            connection.execute(
                "INSERT INTO collaboration_keys(tenant_id,workspace_id,actor_binding_id,purpose,version,key_ref,"
                "status,created_at,revoked_at) VALUES(?,?,?,?,?,?,'active',?,NULL)",
                (*keys, version, reference, now),
            )
            self._audit(connection, keys, version, "rotate", now)
        return {"purpose": keys[3], "version": version, "status": "active"}

    def revoke(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        actor_binding_id: str,
        purpose: str,
        version: int,
    ) -> dict[str, Any]:
        keys = self._keys(tenant_id, workspace_id, actor_binding_id, purpose)
        now = self._clock()
        with sqlite3.connect(self._path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            changed = connection.execute(
                "UPDATE collaboration_keys SET status='revoked',revoked_at=? WHERE tenant_id=? AND workspace_id=? "
                "AND actor_binding_id=? AND purpose=? AND version=? AND status='active'",
                (now, *keys, version),
            ).rowcount
            if changed != 1:
                raise KeyError("collaboration_key_not_active")
            self._audit(connection, keys, version, "revoke", now)
        return {"purpose": keys[3], "version": version, "status": "revoked"}

    def sign(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        actor_binding_id: str,
        payload: bytes,
        purpose: str = "buzz_signing",
    ) -> str:
        keys = self._keys(tenant_id, workspace_id, actor_binding_id, purpose)
        if not isinstance(payload, bytes) or len(payload) > 65_536:
            raise ValueError("collaboration_key_payload_invalid")
        with sqlite3.connect(self._path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT version,key_ref FROM collaboration_keys WHERE tenant_id=? AND workspace_id=? "
                "AND actor_binding_id=? AND purpose=? AND status='active'",
                keys,
            ).fetchone()
            if row is None:
                raise KeyError("collaboration_active_key_missing")
            secret = self._resolve_secret(str(row[1]))
            if not isinstance(secret, bytes) or len(secret) < 32:
                raise ValueError("collaboration_key_material_invalid")
            signature = hmac.new(secret, payload, hashlib.sha256).hexdigest()
            self._audit(connection, keys, int(row[0]), "sign", self._clock())
        return signature

    def verify_audit_chain(self, tenant_id: str, workspace_id: str) -> dict[str, Any]:
        scope = (require_id(tenant_id, "tenant_id"), require_id(workspace_id, "workspace_id"))
        with sqlite3.connect(self._path) as connection:
            rows = connection.execute(
                "SELECT actor_binding_id,purpose,version,operation,occurred_at,previous_digest,entry_digest "
                "FROM collaboration_key_audit WHERE tenant_id=? AND workspace_id=? ORDER BY sequence",
                scope,
            ).fetchall()
        previous = "0" * 64
        for actor, purpose, version, operation, occurred_at, stored_previous, stored_digest in rows:
            body = {
                "tenant_id": scope[0],
                "workspace_id": scope[1],
                "actor_binding_id": actor,
                "purpose": purpose,
                "version": int(version),
                "operation": operation,
                "occurred_at": float(occurred_at),
                "previous_digest": previous,
            }
            if stored_previous != previous or canonical_digest(body) != stored_digest:
                return {"valid": False, "entries": len(rows), "reason_code": "audit_chain_tampered"}
            previous = stored_digest
        return {"valid": True, "entries": len(rows), "head_digest": previous}

    @staticmethod
    def _keys(tenant_id: str, workspace_id: str, actor_binding_id: str, purpose: str) -> tuple[str, ...]:
        return (
            require_id(tenant_id, "tenant_id"),
            require_id(workspace_id, "workspace_id"),
            require_id(actor_binding_id, "actor_binding_id"),
            require_id(purpose, "key_purpose"),
        )

    @staticmethod
    def _audit(
        connection: sqlite3.Connection,
        keys: tuple[str, ...],
        version: int,
        operation: str,
        occurred_at: float,
    ) -> None:
        previous_row = connection.execute(
            "SELECT entry_digest FROM collaboration_key_audit WHERE tenant_id=? AND workspace_id=? "
            "ORDER BY sequence DESC LIMIT 1",
            keys[:2],
        ).fetchone()
        previous = str(previous_row[0]) if previous_row else "0" * 64
        body = {
            "tenant_id": keys[0],
            "workspace_id": keys[1],
            "actor_binding_id": keys[2],
            "purpose": keys[3],
            "version": version,
            "operation": operation,
            "occurred_at": float(occurred_at),
            "previous_digest": previous,
        }
        connection.execute(
            "INSERT INTO collaboration_key_audit(tenant_id,workspace_id,actor_binding_id,purpose,version,"
            "operation,occurred_at,previous_digest,entry_digest) VALUES(?,?,?,?,?,?,?,?,?)",
            (*keys, version, operation, float(occurred_at), previous, canonical_digest(body)),
        )


__all__ = ["CollaborationKeyCustody"]
