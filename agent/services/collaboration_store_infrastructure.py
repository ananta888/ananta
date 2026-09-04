"""SQLite lifecycle, workspace reads and recovery for collaboration state."""

# SQL statements retain complete column/key declarations on one line for auditability.
# ruff: noqa: E501

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from agent.services.collaboration_workspace_store_contracts import CollaborationStoreConflict
from ananta_contracts.collaboration_workspace import canonical_json, require_id


class CollaborationStoreInfrastructureMixin:
    """Own schema lifecycle, aggregate reads, backup and recovery."""

    def get_workspace(self, tenant_id: str, workspace_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = self._workspace_row(
                connection, require_id(tenant_id, "tenant_id"), require_id(workspace_id, "workspace_id")
            )
            rooms = connection.execute(
                "SELECT r.payload_json,a.access_mode,a.revision,l.state,l.revision,l.snapshot_digest "
                "FROM collaboration_rooms r "
                "JOIN collaboration_room_access a ON a.tenant_id=r.tenant_id "
                "AND a.workspace_id=r.workspace_id AND a.room_id=r.room_id "
                "JOIN collaboration_room_lifecycle l ON l.tenant_id=r.tenant_id "
                "AND l.workspace_id=r.workspace_id AND l.room_id=r.room_id "
                "WHERE r.tenant_id=? AND r.workspace_id=? ORDER BY r.room_id",
                (tenant_id, workspace_id),
            ).fetchall()
            members = connection.execute(
                "SELECT m.payload_json,a.payload_json FROM collaboration_memberships m JOIN collaboration_actors a "
                "ON a.tenant_id=m.tenant_id AND a.actor_binding_id=m.actor_binding_id "
                "WHERE m.tenant_id=? AND m.workspace_id=? ORDER BY m.actor_binding_id",
                (tenant_id, workspace_id),
            ).fetchall()
        value = json.loads(row[0])
        value["rooms"] = [
            {
                **json.loads(item[0]),
                "access_mode": item[1],
                "access_revision": int(item[2]),
                "lifecycle_state": item[3],
                "lifecycle_revision": int(item[4]),
                "snapshot_digest": item[5],
            }
            for item in rooms
        ]
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

    def operational_snapshot(self, tenant_id: str, workspace_id: str) -> dict[str, int]:
        """Return bounded, content-free workspace counters for observability."""

        tenant = require_id(tenant_id, "tenant_id")
        workspace = require_id(workspace_id, "workspace_id")
        with self._connect() as connection:
            self._workspace_row(connection, tenant, workspace)
            event_count, latest_sequence = connection.execute(
                "SELECT COUNT(*),COALESCE(MAX(sequence),0) FROM collaboration_events "
                "WHERE tenant_id=? AND workspace_id=?",
                (tenant, workspace),
            ).fetchone()
            pending_outbox = connection.execute(
                "SELECT COUNT(*) FROM collaboration_outbox o JOIN collaboration_events e "
                "ON e.tenant_id=o.tenant_id AND e.event_id=o.event_id "
                "WHERE e.tenant_id=? AND e.workspace_id=? AND o.status!='completed'",
                (tenant, workspace),
            ).fetchone()[0]
            retry_outbox = connection.execute(
                "SELECT COUNT(*) FROM collaboration_outbox o JOIN collaboration_events e "
                "ON e.tenant_id=o.tenant_id AND e.event_id=o.event_id "
                "WHERE e.tenant_id=? AND e.workspace_id=? AND o.status='retry'",
                (tenant, workspace),
            ).fetchone()[0]
            inbox_count = connection.execute(
                "SELECT COUNT(*) FROM collaboration_inbox WHERE tenant_id=?",
                (tenant,),
            ).fetchone()[0]
            projection_rows = connection.execute(
                "SELECT projection_name,checkpoint FROM collaboration_projection_checkpoints "
                "WHERE tenant_id=? AND workspace_id=?",
                (tenant, workspace),
            ).fetchall()
            search = connection.execute(
                "SELECT checkpoint FROM collaboration_search_manifests WHERE tenant_id=? AND workspace_id=?",
                (tenant, workspace),
            ).fetchone()
        projection_lag = max([int(latest_sequence) - int(row[1]) for row in projection_rows] or [int(latest_sequence)])
        search_lag = int(latest_sequence) - int(search[0]) if search else int(latest_sequence)
        return {
            "events": int(event_count),
            "latest_sequence": int(latest_sequence),
            "outbox_pending": int(pending_outbox),
            "outbox_retry": int(retry_outbox),
            "inbox_admitted": int(inbox_count),
            "projection_lag": max(0, projection_lag),
            "search_lag": max(0, search_lag),
        }
    @staticmethod
    def _upsert_actor(connection: sqlite3.Connection, tenant_id: str, actor: Mapping[str, Any]) -> None:
        current = connection.execute(
            "SELECT payload_json FROM collaboration_actors WHERE tenant_id=? AND actor_binding_id=?",
            (tenant_id, actor["actor_binding_id"]),
        ).fetchone()
        if current:
            existing = json.loads(current[0])
            identity_fields = ("actor_kind", "authority_kind", "authority_subject")
            if any(existing[field] != actor[field] for field in identity_fields):
                raise CollaborationStoreConflict("collaboration_actor_binding_conflict")
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

    @staticmethod
    def _thread_projection(
        connection: sqlite3.Connection, tenant_id: str, workspace_id: str, thread_id: str
    ) -> dict[str, Any]:
        rows = connection.execute(
            "SELECT event_id,room_id,event_type FROM collaboration_events "
            "WHERE tenant_id=? AND workspace_id=? AND (event_id=? OR thread_id=?) ORDER BY sequence",
            (tenant_id, workspace_id, thread_id, thread_id),
        ).fetchall()
        if not rows or rows[0][0] != thread_id or rows[0][2] != "message.posted":
            raise KeyError("collaboration_thread_not_found")
        status = "open"
        for _event_id, _room_id, event_type in rows[1:]:
            if event_type == "thread.resolved":
                status = "resolved"
            elif event_type == "thread.reopened":
                status = "open"
            elif event_type == "thread.tombstoned":
                status = "tombstoned"
        return {
            "thread_id": thread_id,
            "room_id": rows[0][1],
            "status": status,
            "revision": len(rows),
        }

    def _transition_outbox(
        self,
        tenant_id: str,
        event_id: str,
        attempt_id: str,
        *,
        status: str,
        next_attempt_at: float,
        error_code: str | None,
        completed_at: float | None,
    ) -> dict[str, Any]:
        keys = (
            require_id(tenant_id, "tenant_id"),
            require_id(event_id, "event_id"),
            require_id(attempt_id, "attempt_id"),
        )
        with self._transaction, self._connect() as connection:
            row = connection.execute(
                "SELECT status,attempt_id,attempts FROM collaboration_outbox WHERE tenant_id=? AND event_id=?",
                keys[:2],
            ).fetchone()
            if row is None:
                raise KeyError("collaboration_outbox_event_not_found")
            if row[0] != "delivering" or row[1] != keys[2]:
                raise CollaborationStoreConflict("collaboration_outbox_attempt_conflict")
            connection.execute(
                "UPDATE collaboration_outbox SET status=?,next_attempt_at=?,last_error_code=?,completed_at=?,"
                "leased_by=NULL,lease_expires_at=0 WHERE tenant_id=? AND event_id=?",
                (status, next_attempt_at, error_code, completed_at, *keys[:2]),
            )
        return {
            "tenant_id": keys[0],
            "event_id": keys[1],
            "attempt_id": keys[2],
            "attempt": int(row[2]),
            "status": status,
            "next_attempt_at": next_attempt_at,
            "error_code": error_code,
        }

    @staticmethod
    def _project(projection_name: str, events: list[dict[str, Any]]) -> Any:
        if projection_name == "timeline":
            return [{"event_id": event["event_id"], "sequence": event["sequence"]} for event in events]
        if projection_name == "search":
            return [
                {
                    "event_id": event["event_id"],
                    "sequence": event["sequence"],
                    "text": canonical_json(event.get("payload") or {}).casefold(),
                }
                for event in events
                if event["event_type"] != "thread.tombstoned"
            ]
        threads: dict[str, dict[str, Any]] = {}
        for event in events:
            if event["event_type"] == "message.posted":
                threads[event["event_id"]] = {
                    "thread_id": event["event_id"],
                    "room_id": event.get("room_id"),
                    "status": "open",
                    "revision": 1,
                }
                continue
            root = threads.get(event.get("thread_id"))
            if root is None:
                continue
            root["revision"] += 1
            if event["event_type"] == "thread.resolved":
                root["status"] = "resolved"
            elif event["event_type"] == "thread.reopened":
                root["status"] = "open"
            elif event["event_type"] == "thread.tombstoned":
                root["status"] = "tombstoned"
        return [threads[key] for key in sorted(threads)]

    def _initialize(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS collaboration_workspaces(tenant_id TEXT,workspace_id TEXT,revision INTEGER,payload_json TEXT,PRIMARY KEY(tenant_id,workspace_id));
                CREATE TABLE IF NOT EXISTS collaboration_actors(tenant_id TEXT,actor_binding_id TEXT,payload_json TEXT,PRIMARY KEY(tenant_id,actor_binding_id));
                CREATE TABLE IF NOT EXISTS collaboration_memberships(tenant_id TEXT,workspace_id TEXT,actor_binding_id TEXT,revision INTEGER,role TEXT,status TEXT,payload_json TEXT,PRIMARY KEY(tenant_id,workspace_id,actor_binding_id));
                CREATE TABLE IF NOT EXISTS collaboration_membership_history(tenant_id TEXT,workspace_id TEXT,actor_binding_id TEXT,revision INTEGER,payload_json TEXT,PRIMARY KEY(tenant_id,workspace_id,actor_binding_id,revision));
                CREATE TABLE IF NOT EXISTS collaboration_external_identities(tenant_id TEXT,actor_binding_id TEXT,provider TEXT,external_subject TEXT,key_fingerprint TEXT,revision INTEGER,status TEXT,payload_json TEXT,PRIMARY KEY(tenant_id,actor_binding_id,provider));
                CREATE UNIQUE INDEX IF NOT EXISTS collaboration_external_identity_subject ON collaboration_external_identities(tenant_id,provider,external_subject) WHERE status='active';
                CREATE TABLE IF NOT EXISTS collaboration_external_identity_history(tenant_id TEXT,actor_binding_id TEXT,provider TEXT,revision INTEGER,payload_json TEXT,PRIMARY KEY(tenant_id,actor_binding_id,provider,revision));
                CREATE TABLE IF NOT EXISTS collaboration_resource_offers(tenant_id TEXT,workspace_id TEXT,offer_id TEXT,owner_actor_binding_id TEXT,resource_id TEXT,status TEXT,expires_at REAL,payload_digest TEXT,payload_json TEXT,PRIMARY KEY(tenant_id,workspace_id,offer_id));
                CREATE TABLE IF NOT EXISTS collaboration_agent_intents(tenant_id TEXT,workspace_id TEXT,intent_id TEXT,correlation_id TEXT,causation_id TEXT,hop_count INTEGER,state TEXT,payload_digest TEXT,payload_json TEXT,PRIMARY KEY(tenant_id,workspace_id,intent_id));
                CREATE INDEX IF NOT EXISTS collaboration_agent_intent_correlation ON collaboration_agent_intents(tenant_id,workspace_id,correlation_id);
                CREATE TABLE IF NOT EXISTS collaboration_resource_leases(tenant_id TEXT,workspace_id TEXT,lease_id TEXT,resource_id TEXT,task_id TEXT,assignment_id TEXT,fencing_token INTEGER,status TEXT,expires_at REAL,payload_digest TEXT,payload_json TEXT,PRIMARY KEY(tenant_id,workspace_id,lease_id));
                CREATE TABLE IF NOT EXISTS collaboration_command_decisions(tenant_id TEXT,workspace_id TEXT,request_id TEXT,task_id TEXT,binding_digest TEXT,state TEXT,policy_revision INTEGER,payload_json TEXT,PRIMARY KEY(tenant_id,workspace_id,request_id));
                CREATE TABLE IF NOT EXISTS collaboration_admission_quotas(tenant_id TEXT,workspace_id TEXT,actor_binding_id TEXT,category TEXT,window_start INTEGER,count INTEGER,PRIMARY KEY(tenant_id,workspace_id,actor_binding_id,category,window_start));
                CREATE TABLE IF NOT EXISTS collaboration_rooms(tenant_id TEXT,workspace_id TEXT,room_id TEXT,payload_json TEXT,PRIMARY KEY(tenant_id,workspace_id,room_id));
                CREATE TABLE IF NOT EXISTS collaboration_room_access(tenant_id TEXT,workspace_id TEXT,room_id TEXT,access_mode TEXT,revision INTEGER,PRIMARY KEY(tenant_id,workspace_id,room_id));
                CREATE TABLE IF NOT EXISTS collaboration_room_memberships(tenant_id TEXT,workspace_id TEXT,room_id TEXT,actor_binding_id TEXT,PRIMARY KEY(tenant_id,workspace_id,room_id,actor_binding_id));
                CREATE TABLE IF NOT EXISTS collaboration_room_lifecycle(tenant_id TEXT,workspace_id TEXT,room_id TEXT,state TEXT,revision INTEGER,snapshot_digest TEXT,payload_json TEXT,PRIMARY KEY(tenant_id,workspace_id,room_id));
                CREATE TABLE IF NOT EXISTS collaboration_room_bindings(tenant_id TEXT,workspace_id TEXT,room_id TEXT,binding_kind TEXT,binding_id TEXT,revision INTEGER,binding_digest TEXT,payload_json TEXT,PRIMARY KEY(tenant_id,workspace_id,room_id),UNIQUE(tenant_id,workspace_id,binding_kind,binding_id));
                CREATE TABLE IF NOT EXISTS collaboration_events(tenant_id TEXT,workspace_id TEXT,sequence INTEGER,event_id TEXT,idempotency_key TEXT,room_id TEXT,thread_id TEXT,event_type TEXT,actor_binding_id TEXT,causation_id TEXT,visibility TEXT,occurred_at REAL,admitted_at REAL,payload_json TEXT,PRIMARY KEY(tenant_id,workspace_id,sequence),UNIQUE(tenant_id,workspace_id,event_id),UNIQUE(tenant_id,workspace_id,idempotency_key));
                CREATE TABLE IF NOT EXISTS collaboration_outbox(tenant_id TEXT,event_id TEXT,topic TEXT,status TEXT,payload_json TEXT,attempt_id TEXT,attempts INTEGER NOT NULL DEFAULT 0,leased_by TEXT,lease_expires_at REAL NOT NULL DEFAULT 0,next_attempt_at REAL NOT NULL DEFAULT 0,last_error_code TEXT,completed_at REAL,PRIMARY KEY(tenant_id,event_id));
                CREATE TABLE IF NOT EXISTS collaboration_inbox(tenant_id TEXT,adapter_id TEXT,external_event_id TEXT,payload_digest TEXT,origin TEXT,mapping_version TEXT,admitted_at REAL,PRIMARY KEY(tenant_id,adapter_id,external_event_id));
                CREATE TABLE IF NOT EXISTS collaboration_cursors(tenant_id TEXT,workspace_id TEXT,room_id TEXT,actor_binding_id TEXT,sequence INTEGER,PRIMARY KEY(tenant_id,workspace_id,room_id,actor_binding_id));
                CREATE TABLE IF NOT EXISTS collaboration_presence(tenant_id TEXT,workspace_id TEXT,actor_binding_id TEXT,epoch INTEGER,payload_json TEXT,PRIMARY KEY(tenant_id,workspace_id,actor_binding_id));
                CREATE TABLE IF NOT EXISTS collaboration_security_epochs(tenant_id TEXT,workspace_id TEXT,epoch INTEGER,PRIMARY KEY(tenant_id,workspace_id));
                CREATE TABLE IF NOT EXISTS collaboration_projection_checkpoints(tenant_id TEXT,workspace_id TEXT,projection_name TEXT,checkpoint INTEGER,state_digest TEXT,payload_json TEXT,PRIMARY KEY(tenant_id,workspace_id,projection_name));
                CREATE TABLE IF NOT EXISTS collaboration_search_documents(tenant_id TEXT,workspace_id TEXT,event_id TEXT,room_id TEXT,sequence INTEGER,document_text TEXT,payload_json TEXT,PRIMARY KEY(tenant_id,workspace_id,event_id));
                CREATE INDEX IF NOT EXISTS collaboration_search_text_scope ON collaboration_search_documents(tenant_id,workspace_id,sequence);
                CREATE TABLE IF NOT EXISTS collaboration_search_manifests(tenant_id TEXT,workspace_id TEXT,checkpoint INTEGER,index_digest TEXT,payload_json TEXT,PRIMARY KEY(tenant_id,workspace_id));
                """
            )
            self._migrate_event_columns(connection)
            self._migrate_delivery_columns(connection)
            self._backfill_room_access(connection)
            self._backfill_room_lifecycle(connection)
            connection.execute(
                "INSERT INTO collaboration_security_epochs(tenant_id,workspace_id,epoch) "
                "SELECT tenant_id,workspace_id,1 FROM collaboration_workspaces WHERE NOT EXISTS "
                "(SELECT 1 FROM collaboration_security_epochs e WHERE e.tenant_id=collaboration_workspaces.tenant_id "
                "AND e.workspace_id=collaboration_workspaces.workspace_id)"
            )

    @staticmethod
    def _migrate_event_columns(connection: sqlite3.Connection) -> None:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(collaboration_events)")}
        definitions = {
            "room_id": "TEXT",
            "thread_id": "TEXT",
            "event_type": "TEXT",
            "actor_binding_id": "TEXT",
            "causation_id": "TEXT",
            "visibility": "TEXT",
            "occurred_at": "REAL",
            "admitted_at": "REAL",
        }
        for name, definition in definitions.items():
            if name not in columns:
                connection.execute(f"ALTER TABLE collaboration_events ADD COLUMN {name} {definition}")
        rows = connection.execute(
            "SELECT tenant_id,workspace_id,sequence,payload_json FROM collaboration_events "
            "WHERE event_type IS NULL OR visibility IS NULL OR admitted_at IS NULL OR occurred_at IS NULL"
        ).fetchall()
        for tenant_id, workspace_id, sequence, payload_json in rows:
            payload = json.loads(payload_json)
            admitted_at = payload.get("admitted_at", payload.get("occurred_at", 0.0))
            payload["admitted_at"] = float(admitted_at)
            connection.execute(
                "UPDATE collaboration_events SET room_id=?,thread_id=?,event_type=?,actor_binding_id=?,"
                "causation_id=?,visibility=?,occurred_at=?,admitted_at=?,payload_json=? "
                "WHERE tenant_id=? AND workspace_id=? AND sequence=?",
                (
                    payload.get("room_id"),
                    payload.get("thread_id"),
                    payload.get("event_type"),
                    payload.get("actor_binding_id"),
                    payload.get("causation_id"),
                    payload.get("visibility", "workspace"),
                    float(payload.get("occurred_at", admitted_at)),
                    float(admitted_at),
                    canonical_json(payload),
                    tenant_id,
                    workspace_id,
                    sequence,
                ),
            )

    @staticmethod
    def _backfill_room_access(connection: sqlite3.Connection) -> None:
        connection.execute(
            "INSERT INTO collaboration_room_access(tenant_id,workspace_id,room_id,access_mode,revision) "
            "SELECT r.tenant_id,r.workspace_id,r.room_id,'workspace',1 FROM collaboration_rooms r "
            "WHERE NOT EXISTS (SELECT 1 FROM collaboration_room_access a WHERE a.tenant_id=r.tenant_id "
            "AND a.workspace_id=r.workspace_id AND a.room_id=r.room_id)"
        )

    @staticmethod
    def _backfill_room_lifecycle(connection: sqlite3.Connection) -> None:
        rows = connection.execute(
            "SELECT r.tenant_id,r.workspace_id,r.room_id FROM collaboration_rooms r WHERE NOT EXISTS "
            "(SELECT 1 FROM collaboration_room_lifecycle l WHERE l.tenant_id=r.tenant_id "
            "AND l.workspace_id=r.workspace_id AND l.room_id=r.room_id)"
        ).fetchall()
        for tenant_id, workspace_id, room_id in rows:
            connection.execute(
                "INSERT INTO collaboration_room_lifecycle(tenant_id,workspace_id,room_id,state,revision,"
                "snapshot_digest,payload_json) VALUES(?,?,?,?,?,?,?)",
                (
                    tenant_id,
                    workspace_id,
                    room_id,
                    "active",
                    1,
                    None,
                    canonical_json({"room_id": room_id, "state": "active", "revision": 1}),
                ),
            )

    @staticmethod
    def _migrate_delivery_columns(connection: sqlite3.Connection) -> None:
        tables = {
            "collaboration_outbox": {
                "attempt_id": "TEXT",
                "attempts": "INTEGER NOT NULL DEFAULT 0",
                "leased_by": "TEXT",
                "lease_expires_at": "REAL NOT NULL DEFAULT 0",
                "next_attempt_at": "REAL NOT NULL DEFAULT 0",
                "last_error_code": "TEXT",
                "completed_at": "REAL",
            },
            "collaboration_inbox": {
                "origin": "TEXT",
                "mapping_version": "TEXT",
                "admitted_at": "REAL",
            },
        }
        for table, definitions in tables.items():
            columns = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
            for name, definition in definitions.items():
                if name not in columns:
                    connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path, timeout=5.0)

    def backup_to(self, destination: str | Path) -> dict[str, Any]:
        target = Path(destination)
        if not target.name or target.resolve() == self._path.resolve():
            raise ValueError("collaboration_backup_destination_invalid")
        target.parent.mkdir(parents=True, exist_ok=True)
        with self._transaction, self._connect() as source, sqlite3.connect(target) as backup:
            source.backup(backup)
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        with sqlite3.connect(target) as connection:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            sequence = int(
                connection.execute("SELECT COALESCE(MAX(sequence),0) FROM collaboration_events").fetchone()[0]
            )
        if integrity != "ok":
            raise RuntimeError("collaboration_backup_integrity_failed")
        return {
            "schema": "ananta.collaboration-backup.v1",
            "filename": target.name,
            "digest": digest,
            "event_sequence": sequence,
            "integrity": "ok",
        }

    def restore_from(self, source: str | Path, *, expected_digest: str) -> dict[str, Any]:
        backup = Path(source)
        if not backup.is_file() or hashlib.sha256(backup.read_bytes()).hexdigest() != expected_digest:
            raise ValueError("collaboration_restore_digest_mismatch")
        with sqlite3.connect(backup) as connection:
            if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("collaboration_restore_integrity_failed")
            required = {"collaboration_workspaces", "collaboration_events", "collaboration_memberships"}
            tables = {
                row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            }
            if not required <= tables:
                raise ValueError("collaboration_restore_schema_invalid")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self._path.name}.", suffix=".restore", dir=self._path.parent
        )
        os.close(descriptor)
        temporary = Path(temporary_name)
        rollback = self._path.with_suffix(f"{self._path.suffix}.rollback")
        sidecars = [
            (Path(f"{self._path}-wal"), Path(f"{rollback}-wal")),
            (Path(f"{self._path}-shm"), Path(f"{rollback}-shm")),
        ]
        try:
            with sqlite3.connect(backup) as source_connection, sqlite3.connect(temporary) as target_connection:
                source_connection.backup(target_connection)
            with self._transaction:
                if self._path.exists():
                    os.replace(self._path, rollback)
                for current_sidecar, rollback_sidecar in sidecars:
                    if current_sidecar.exists():
                        os.replace(current_sidecar, rollback_sidecar)
                os.replace(temporary, self._path)
            self._initialize()
        except Exception:
            temporary.unlink(missing_ok=True)
            if rollback.exists() and not self._path.exists():
                os.replace(rollback, self._path)
                for current_sidecar, rollback_sidecar in sidecars:
                    if rollback_sidecar.exists():
                        os.replace(rollback_sidecar, current_sidecar)
            raise
        return {
            "schema": "ananta.collaboration-restore.v1",
            "digest": expected_digest,
            "rollback_filename": rollback.name if rollback.exists() else None,
            "restored": True,
        }
