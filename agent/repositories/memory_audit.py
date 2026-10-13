"""Transactional append-only memory decision audit, with independently checked chain."""

from __future__ import annotations

import hashlib
import json

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from agent.db_models.memory_audit import MemoryAuditEventDB, MemoryAuditHeadDB
from agent.repositories.sqlalchemy_support import SQLAlchemyStoreSupport
from ananta_contracts.memory_access import require_memory_identifier, require_memory_timestamp

ZERO_HASH = "0" * 64


def _digest(tenant_id, sequence, payload, previous_hash, created_at, retention_until):
    body = {"tenant_id": tenant_id, "sequence": sequence, "payload": payload, "previous_hash": previous_hash,
            "created_at": created_at, "retention_until": retention_until}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _event_digest(event):
    return _digest(event.tenant_id, event.sequence, event.payload, event.previous_hash,
                   event.created_at, event.retention_until)


def _event_indexes_match(event):
    if not isinstance(event.payload, dict):
        return False
    target = event.payload.get("target") or {}
    if not isinstance(target, dict):
        return False
    namespace = target.get("namespace") or {}
    if not isinstance(namespace, dict):
        return False
    return event.task_id == namespace.get("task_id") and event.trace_id == event.payload.get("trace_id")


def validate_memory_audit_event(event):
    if (event is None or _event_digest(event) != event.event_hash or not _event_indexes_match(event)
            or event.id != f"memory-audit-{event.event_hash}"):
        raise ValueError("memory_audit_chain_invalid")


class SQLMemoryAuditRepository:
    """No update/delete API; serialize append under the tenant head lock."""

    def __init__(self, bind):
        self._sql = SQLAlchemyStoreSupport(bind)
        if self._sql._dialect_name not in {"sqlite", "postgresql"}:
            raise ValueError("memory_audit_database_unsupported")

    def append(self, payload: dict, *, created_at: float, retention_until: float) -> dict:
        with self._sql._transaction() as session:
            return self.append_in_transaction(session, payload, created_at=created_at,
                                              retention_until=retention_until)

    def lock_tenant(self, session, tenant_id):
        """Reserve the same tenant audit aggregate in an owning SQL transaction."""
        require_memory_identifier(tenant_id, "tenant_id")
        self._guard_snapshot(session, tenant_id)

    def append_in_transaction(self, session, payload: dict, *, created_at: float, retention_until: float) -> dict:
        """Caller owns commit/rollback; a snapshot and its audit link commit together."""
        # Canonical JSON also takes an independent snapshot of nested caller-owned data.
        payload = json.loads(json.dumps(payload, sort_keys=True, allow_nan=False))
        tenant_id = require_memory_identifier(payload["actor"]["tenant_id"], "tenant_id")
        require_memory_timestamp(created_at, "audit_clock")
        require_memory_timestamp(retention_until, "audit_retention")
        if retention_until <= created_at:
            raise ValueError("memory_audit_retention_invalid")
        created_at, retention_until = float(created_at), float(retention_until)
        self._guard_snapshot(session, tenant_id)
        insert = sqlite_insert if self._sql._dialect_name == "sqlite" else pg_insert
        session.execute(insert(MemoryAuditHeadDB).values(
            tenant_id=tenant_id, last_sequence=0, last_hash=ZERO_HASH,
        ).on_conflict_do_nothing(index_elements=["tenant_id"]))
        statement = select(MemoryAuditHeadDB).where(MemoryAuditHeadDB.tenant_id == tenant_id)
        head = session.execute(self._sql._for_update(statement)).scalar_one()
        if head.last_sequence:
            tail = session.execute(select(MemoryAuditEventDB).where(
                MemoryAuditEventDB.tenant_id == tenant_id, MemoryAuditEventDB.sequence == head.last_sequence,
            )).scalar_one_or_none()
            if (tail is None or tail.event_hash != head.last_hash or _event_digest(tail) != tail.event_hash
                    or not _event_indexes_match(tail)):
                raise ValueError("memory_audit_chain_invalid")
        elif head.last_hash != ZERO_HASH:
            raise ValueError("memory_audit_chain_invalid")
        sequence = head.last_sequence + 1
        digest = _digest(tenant_id, sequence, payload, head.last_hash, created_at, retention_until)
        target = payload.get("target") or {}
        namespace = target.get("namespace") or {}
        session.add(MemoryAuditEventDB(
            id=f"memory-audit-{digest}", tenant_id=tenant_id, sequence=sequence,
            task_id=namespace.get("task_id"), trace_id=payload.get("trace_id"),
            previous_hash=head.last_hash, event_hash=digest, payload=payload,
            created_at=created_at, retention_until=retention_until,
        ))
        head.last_sequence, head.last_hash = sequence, digest
        session.flush()
        return {"event_id": f"memory-audit-{digest}", "event_hash": digest, "sequence": sequence}

    def list_events(self, *, tenant_id: str, task_id: str | None = None,
                    trace_id: str | None = None, limit: int = 100) -> list[dict]:
        require_memory_identifier(tenant_id, "tenant_id")
        self._limit(limit)
        statement = select(MemoryAuditEventDB).where(MemoryAuditEventDB.tenant_id == tenant_id)
        if task_id is not None:
            statement = statement.where(MemoryAuditEventDB.task_id == task_id)
        if trace_id is not None:
            statement = statement.where(MemoryAuditEventDB.trace_id == trace_id)
        statement = statement.order_by(MemoryAuditEventDB.sequence).limit(limit)
        with self._sql._read_session() as session:
            rows = session.execute(statement).scalars().all()
            return [json.loads(json.dumps(row.model_dump(), allow_nan=False)) for row in rows]

    def verify(self, *, tenant_id: str, limit: int = 10000) -> dict:
        require_memory_identifier(tenant_id, "tenant_id")
        self._limit(limit)
        with self._sql._transaction() as session:
            self._guard_snapshot(session, tenant_id)
            statement = select(MemoryAuditHeadDB).where(MemoryAuditHeadDB.tenant_id == tenant_id)
            head = session.execute(self._sql._for_update(statement)).scalar_one_or_none()
            rows = session.execute(select(MemoryAuditEventDB).where(
                MemoryAuditEventDB.tenant_id == tenant_id,
            ).order_by(MemoryAuditEventDB.sequence).limit(limit + 1)).scalars().all()
            reason = "memory_audit_chain_verified"
            previous = ZERO_HASH
            if len(rows) > limit:
                reason = "memory_audit_verification_window_exceeded"
            else:
                for sequence, row in enumerate(rows, 1):
                    if (row.sequence != sequence or row.previous_hash != previous
                            or _event_digest(row) != row.event_hash or row.id != f"memory-audit-{row.event_hash}"
                            or not _event_indexes_match(row)):
                        reason = "memory_audit_chain_invalid"
                        break
                    previous = row.event_hash
                if ((head is None and rows) or head is not None
                        and (head.last_sequence != len(rows) or head.last_hash != previous)):
                    reason = "memory_audit_chain_invalid"
            return {"verified": reason == "memory_audit_chain_verified", "reason_code": reason,
                    "checked": min(len(rows), limit), "production_release_evidence": False}

    def project_events(self, *, tenant_id: str, project_id: str, query) -> list[dict]:
        """Apply operator filters in SQL before pagination; never load other scopes."""
        require_memory_identifier(tenant_id, "tenant_id")
        require_memory_identifier(project_id, "project_id")
        query.assert_valid()
        payload = MemoryAuditEventDB.payload
        namespace = payload["target"]["namespace"]
        statement = select(MemoryAuditEventDB).where(
            MemoryAuditEventDB.tenant_id == tenant_id,
            namespace["tenant_id"].as_string() == tenant_id,
            namespace["workspace_id"].as_string() == project_id,
            MemoryAuditEventDB.sequence > query.after_sequence,
        )
        if query.task_id is not None:
            statement = statement.where(namespace["task_id"].as_string() == query.task_id)
        if query.run_id is not None:
            # Audit Run is the accessing Run, not a source entry's older Run.
            statement = statement.where(payload["run_id"].as_string() == query.run_id)
        if query.trace_id is not None:
            statement = statement.where(MemoryAuditEventDB.trace_id == query.trace_id)
        if query.channel_id is not None:
            statement = statement.where(payload["target"]["channel_id"].as_string() == query.channel_id)
        if query.scope is not None:
            statement = statement.where(payload["target"]["security"]["scope"].as_string() == query.scope.value)
        statement = statement.order_by(MemoryAuditEventDB.sequence).limit(query.page_size + 1)
        with self._sql._read_session() as session:
            rows = session.execute(statement).scalars().all()
            if any(_event_digest(row) != row.event_hash or not _event_indexes_match(row)
                   or row.id != f"memory-audit-{row.event_hash}" for row in rows):
                raise ValueError("memory_audit_chain_invalid")
            return [json.loads(json.dumps(row.model_dump(), allow_nan=False)) for row in rows]

    def _guard_snapshot(self, session, tenant_id):
        if self._sql._dialect_name == "sqlite":
            # A no-op DML statement reserves the SQLite write transaction, including
            # empty tenants. This also serializes separate Engines/processes.
            session.execute(update(MemoryAuditHeadDB).where(MemoryAuditHeadDB.tenant_id == tenant_id)
                            .values(last_hash=MemoryAuditHeadDB.last_hash))
        else:
            key = int.from_bytes(hashlib.sha256(f"memory-audit:{tenant_id}".encode()).digest()[:8], "big", signed=True)
            session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})

    @staticmethod
    def _limit(value):
        if type(value) is not int or not 1 <= value <= 10000:
            raise ValueError("memory_audit_query_limit_invalid")
