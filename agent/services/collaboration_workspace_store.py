"""Tenant-scoped append-only collaboration store with outbox and dedupe."""

# SQL statements retain complete column/key declarations on one line for auditability.
# ruff: noqa: E501

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from agent.services.interprocess_file_transaction import InterProcessFileTransaction
from ananta_contracts.collaboration_workspace import canonical_json, require_id


class CollaborationStoreConflict(RuntimeError):
    pass


class CollaborationWorkspaceStore:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._transaction = InterProcessFileTransaction(self._path.with_suffix(".lock"))
        self._initialize()

    def create_workspace(self, workspace: Mapping[str, Any], owner: Mapping[str, Any]) -> dict[str, Any]:
        tenant = require_id(workspace.get("tenant_id"), "tenant_id")
        workspace_id = require_id(workspace.get("workspace_id"), "workspace_id")
        with self._transaction, self._connect() as connection:
            if connection.execute(
                "SELECT 1 FROM collaboration_workspaces WHERE tenant_id=? AND workspace_id=?",
                (tenant, workspace_id),
            ).fetchone():
                raise CollaborationStoreConflict("collaboration_workspace_exists")
            connection.execute(
                "INSERT INTO collaboration_workspaces(tenant_id,workspace_id,revision,payload_json) VALUES(?,?,?,?)",
                (tenant, workspace_id, 1, canonical_json({**dict(workspace), "revision": 1})),
            )
            self._upsert_actor(connection, tenant, owner)
            connection.execute(
                "INSERT INTO collaboration_memberships(tenant_id,workspace_id,actor_binding_id,revision,role,status,payload_json) "
                "VALUES(?,?,?,?,?,?,?)",
                (
                    tenant,
                    workspace_id,
                    owner["actor_binding_id"],
                    1,
                    "owner",
                    "active",
                    canonical_json(
                        {
                            "actor_binding_id": owner["actor_binding_id"],
                            "role": "owner",
                            "status": "active",
                            "revision": 1,
                        }
                    ),
                ),
            )
        return self.get_workspace(tenant, workspace_id)

    def add_room(self, tenant_id: str, workspace_id: str, room: Mapping[str, Any]) -> dict[str, Any]:
        tenant = require_id(tenant_id, "tenant_id")
        workspace = require_id(workspace_id, "workspace_id")
        room_id = require_id(room.get("room_id"), "room_id")
        with self._transaction, self._connect() as connection:
            self._workspace_row(connection, tenant, workspace)
            try:
                connection.execute(
                    "INSERT INTO collaboration_rooms(tenant_id,workspace_id,room_id,payload_json) VALUES(?,?,?,?)",
                    (tenant, workspace, room_id, canonical_json(dict(room))),
                )
            except sqlite3.IntegrityError as exc:
                raise CollaborationStoreConflict("collaboration_room_exists") from exc
        return dict(room)

    def put_membership(
        self,
        tenant_id: str,
        workspace_id: str,
        actor: Mapping[str, Any],
        *,
        role: str,
        status: str,
        expected_revision: int | None,
    ) -> dict[str, Any]:
        tenant = require_id(tenant_id, "tenant_id")
        workspace = require_id(workspace_id, "workspace_id")
        actor_id = require_id(actor.get("actor_binding_id"), "actor_binding_id")
        if role not in {"owner", "editor", "viewer"} or status not in {"active", "revoked"}:
            raise ValueError("collaboration_membership_invalid")
        with self._transaction, self._connect() as connection:
            self._workspace_row(connection, tenant, workspace)
            current = connection.execute(
                "SELECT revision FROM collaboration_memberships WHERE tenant_id=? AND workspace_id=? AND actor_binding_id=?",
                (tenant, workspace, actor_id),
            ).fetchone()
            actual = int(current[0]) if current else 0
            if expected_revision is not None and actual != expected_revision:
                raise CollaborationStoreConflict("collaboration_membership_revision_conflict")
            revision = actual + 1
            self._upsert_actor(connection, tenant, actor)
            payload = {"actor_binding_id": actor_id, "role": role, "status": status, "revision": revision}
            connection.execute(
                "INSERT INTO collaboration_memberships(tenant_id,workspace_id,actor_binding_id,revision,role,status,payload_json) "
                "VALUES(?,?,?,?,?,?,?) ON CONFLICT(tenant_id,workspace_id,actor_binding_id) DO UPDATE SET "
                "revision=excluded.revision,role=excluded.role,status=excluded.status,payload_json=excluded.payload_json",
                (tenant, workspace, actor_id, revision, role, status, canonical_json(payload)),
            )
        return payload

    def membership(self, tenant_id: str, workspace_id: str, actor_binding_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM collaboration_memberships WHERE tenant_id=? AND workspace_id=? AND actor_binding_id=?",
                (
                    require_id(tenant_id, "tenant_id"),
                    require_id(workspace_id, "workspace_id"),
                    require_id(actor_binding_id, "actor_binding_id"),
                ),
            ).fetchone()
        return json.loads(row[0]) if row else None

    def append_event(self, tenant_id: str, event: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
        tenant = require_id(tenant_id, "tenant_id")
        workspace = require_id(event.get("workspace_id"), "workspace_id")
        event_id = require_id(event.get("event_id"), "event_id")
        idempotency = require_id(event.get("idempotency_key"), "idempotency_key")
        with self._transaction, self._connect() as connection:
            self._workspace_row(connection, tenant, workspace)
            existing = connection.execute(
                "SELECT payload_json FROM collaboration_events WHERE tenant_id=? AND workspace_id=? AND idempotency_key=?",
                (tenant, workspace, idempotency),
            ).fetchone()
            if existing:
                value = json.loads(existing[0])
                if value["event_id"] != event_id or value["payload_digest"] != event["payload_digest"]:
                    raise CollaborationStoreConflict("collaboration_event_idempotency_conflict")
                return value, True
            sequence = int(
                connection.execute(
                    "SELECT COALESCE(MAX(sequence),0)+1 FROM collaboration_events WHERE tenant_id=? AND workspace_id=?",
                    (tenant, workspace),
                ).fetchone()[0]
            )
            value = {**dict(event), "tenant_id": tenant, "sequence": sequence}
            connection.execute(
                "INSERT INTO collaboration_events(tenant_id,workspace_id,sequence,event_id,idempotency_key,payload_json) "
                "VALUES(?,?,?,?,?,?)",
                (tenant, workspace, sequence, event_id, idempotency, canonical_json(value)),
            )
            connection.execute(
                "INSERT INTO collaboration_outbox(tenant_id,event_id,topic,status,payload_json) VALUES(?,?,?,?,?)",
                (tenant, event_id, "collaboration.workspace-event.v1", "pending", canonical_json(value)),
            )
        return value, False

    def timeline(
        self, tenant_id: str, workspace_id: str, *, room_id: str | None, after: int, limit: int
    ) -> dict[str, Any]:
        if not 1 <= limit <= 200 or after < 0:
            raise ValueError("collaboration_timeline_page_invalid")
        clauses = ["tenant_id=?", "workspace_id=?", "sequence>?"]
        params: list[Any] = [require_id(tenant_id, "tenant_id"), require_id(workspace_id, "workspace_id"), after]
        if room_id is not None:
            clauses.append("json_extract(payload_json,'$.room_id')=?")
            params.append(require_id(room_id, "room_id"))
        params.append(limit)
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT payload_json FROM collaboration_events WHERE {' AND '.join(clauses)} ORDER BY sequence LIMIT ?",
                params,
            ).fetchall()
        items = [json.loads(row[0]) for row in rows]
        return {"items": items, "next_after": items[-1]["sequence"] if items else after, "limit": limit}

    def search(self, tenant_id: str, workspace_id: str, query: str, *, limit: int) -> dict[str, Any]:
        normalized = str(query or "").strip().casefold()
        if not 2 <= len(normalized) <= 128 or not 1 <= limit <= 50:
            raise ValueError("collaboration_search_query_invalid")
        page = self.timeline(tenant_id, workspace_id, room_id=None, after=0, limit=200)
        items = [
            event for event in page["items"] if normalized in canonical_json(event.get("payload") or {}).casefold()
        ][:limit]
        return {"items": items, "query": normalized, "partial": len(page["items"]) == 200, "limit": limit}

    def acknowledge(
        self, tenant_id: str, workspace_id: str, room_id: str, actor_binding_id: str, sequence: int
    ) -> dict[str, Any]:
        if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 0:
            raise ValueError("collaboration_cursor_sequence_invalid")
        keys = (
            require_id(tenant_id, "tenant_id"),
            require_id(workspace_id, "workspace_id"),
            require_id(room_id, "room_id"),
            require_id(actor_binding_id, "actor_binding_id"),
        )
        with self._transaction, self._connect() as connection:
            current = connection.execute(
                "SELECT sequence FROM collaboration_cursors WHERE tenant_id=? AND workspace_id=? AND room_id=? AND actor_binding_id=?",
                keys,
            ).fetchone()
            if current and sequence < int(current[0]):
                raise CollaborationStoreConflict("collaboration_cursor_regression")
            connection.execute(
                "INSERT INTO collaboration_cursors(tenant_id,workspace_id,room_id,actor_binding_id,sequence) VALUES(?,?,?,?,?) "
                "ON CONFLICT(tenant_id,workspace_id,room_id,actor_binding_id) DO UPDATE SET sequence=excluded.sequence",
                (*keys, sequence),
            )
        return {"room_id": keys[2], "actor_binding_id": keys[3], "sequence": sequence}

    def renew_presence(
        self,
        tenant_id: str,
        workspace_id: str,
        actor_binding_id: str,
        lease_id: str,
        expires_at: float,
        epoch: int,
    ) -> dict[str, Any]:
        if not isinstance(epoch, int) or isinstance(epoch, bool) or epoch < 1:
            raise ValueError("collaboration_presence_epoch_invalid")
        payload = {
            "actor_binding_id": require_id(actor_binding_id, "actor_binding_id"),
            "lease_id": require_id(lease_id, "lease_id"),
            "expires_at": float(expires_at),
            "epoch": epoch,
        }
        keys = (
            require_id(tenant_id, "tenant_id"),
            require_id(workspace_id, "workspace_id"),
            payload["actor_binding_id"],
        )
        with self._transaction, self._connect() as connection:
            current = connection.execute(
                "SELECT epoch FROM collaboration_presence WHERE tenant_id=? AND workspace_id=? AND actor_binding_id=?",
                keys,
            ).fetchone()
            if current and epoch < int(current[0]):
                raise CollaborationStoreConflict("collaboration_presence_epoch_stale")
            connection.execute(
                "INSERT INTO collaboration_presence(tenant_id,workspace_id,actor_binding_id,epoch,payload_json) VALUES(?,?,?,?,?) "
                "ON CONFLICT(tenant_id,workspace_id,actor_binding_id) DO UPDATE SET epoch=excluded.epoch,payload_json=excluded.payload_json",
                (*keys, epoch, canonical_json(payload)),
            )
        return payload

    def get_workspace(self, tenant_id: str, workspace_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = self._workspace_row(
                connection, require_id(tenant_id, "tenant_id"), require_id(workspace_id, "workspace_id")
            )
            rooms = connection.execute(
                "SELECT payload_json FROM collaboration_rooms WHERE tenant_id=? AND workspace_id=? ORDER BY room_id",
                (tenant_id, workspace_id),
            ).fetchall()
            members = connection.execute(
                "SELECT m.payload_json,a.payload_json FROM collaboration_memberships m JOIN collaboration_actors a "
                "ON a.tenant_id=m.tenant_id AND a.actor_binding_id=m.actor_binding_id "
                "WHERE m.tenant_id=? AND m.workspace_id=? ORDER BY m.actor_binding_id",
                (tenant_id, workspace_id),
            ).fetchall()
        value = json.loads(row[0])
        value["rooms"] = [json.loads(item[0]) for item in rooms]
        value["memberships"] = [{**json.loads(item[0]), "actor": json.loads(item[1])} for item in members]
        return value

    def list_workspaces(self, tenant_id: str, actor_binding_id: str, *, limit: int = 100) -> dict[str, Any]:
        if not 1 <= limit <= 100:
            raise ValueError("collaboration_workspace_list_limit_invalid")
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT w.payload_json FROM collaboration_workspaces w JOIN collaboration_memberships m "
                "ON m.tenant_id=w.tenant_id AND m.workspace_id=w.workspace_id "
                "WHERE w.tenant_id=? AND m.actor_binding_id=? AND m.status='active' ORDER BY w.workspace_id LIMIT ?",
                (
                    require_id(tenant_id, "tenant_id"),
                    require_id(actor_binding_id, "actor_binding_id"),
                    limit,
                ),
            ).fetchall()
        return {"items": [json.loads(row[0]) for row in rows], "limit": limit}

    @staticmethod
    def _upsert_actor(connection: sqlite3.Connection, tenant_id: str, actor: Mapping[str, Any]) -> None:
        connection.execute(
            "INSERT INTO collaboration_actors(tenant_id,actor_binding_id,payload_json) VALUES(?,?,?) "
            "ON CONFLICT(tenant_id,actor_binding_id) DO UPDATE SET payload_json=excluded.payload_json",
            (tenant_id, actor["actor_binding_id"], canonical_json(dict(actor))),
        )

    @staticmethod
    def _workspace_row(connection: sqlite3.Connection, tenant_id: str, workspace_id: str):
        row = connection.execute(
            "SELECT payload_json FROM collaboration_workspaces WHERE tenant_id=? AND workspace_id=?",
            (tenant_id, workspace_id),
        ).fetchone()
        if not row:
            raise KeyError("collaboration_workspace_not_found")
        return row

    def _initialize(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS collaboration_workspaces(tenant_id TEXT,workspace_id TEXT,revision INTEGER,payload_json TEXT,PRIMARY KEY(tenant_id,workspace_id));
                CREATE TABLE IF NOT EXISTS collaboration_actors(tenant_id TEXT,actor_binding_id TEXT,payload_json TEXT,PRIMARY KEY(tenant_id,actor_binding_id));
                CREATE TABLE IF NOT EXISTS collaboration_memberships(tenant_id TEXT,workspace_id TEXT,actor_binding_id TEXT,revision INTEGER,role TEXT,status TEXT,payload_json TEXT,PRIMARY KEY(tenant_id,workspace_id,actor_binding_id));
                CREATE TABLE IF NOT EXISTS collaboration_rooms(tenant_id TEXT,workspace_id TEXT,room_id TEXT,payload_json TEXT,PRIMARY KEY(tenant_id,workspace_id,room_id));
                CREATE TABLE IF NOT EXISTS collaboration_events(tenant_id TEXT,workspace_id TEXT,sequence INTEGER,event_id TEXT,idempotency_key TEXT,payload_json TEXT,PRIMARY KEY(tenant_id,workspace_id,sequence),UNIQUE(tenant_id,workspace_id,event_id),UNIQUE(tenant_id,workspace_id,idempotency_key));
                CREATE TABLE IF NOT EXISTS collaboration_outbox(tenant_id TEXT,event_id TEXT,topic TEXT,status TEXT,payload_json TEXT,PRIMARY KEY(tenant_id,event_id));
                CREATE TABLE IF NOT EXISTS collaboration_inbox(tenant_id TEXT,adapter_id TEXT,external_event_id TEXT,payload_digest TEXT,PRIMARY KEY(tenant_id,adapter_id,external_event_id));
                CREATE TABLE IF NOT EXISTS collaboration_cursors(tenant_id TEXT,workspace_id TEXT,room_id TEXT,actor_binding_id TEXT,sequence INTEGER,PRIMARY KEY(tenant_id,workspace_id,room_id,actor_binding_id));
                CREATE TABLE IF NOT EXISTS collaboration_presence(tenant_id TEXT,workspace_id TEXT,actor_binding_id TEXT,epoch INTEGER,payload_json TEXT,PRIMARY KEY(tenant_id,workspace_id,actor_binding_id));
                """
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path, timeout=5.0)


__all__ = ["CollaborationStoreConflict", "CollaborationWorkspaceStore"]
