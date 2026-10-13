import logging
import math
import time
from typing import List, Optional

from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlmodel import Session, select

from agent.db_models import MemoryEntryDB
from agent.repositories.result_memory_scope import result_memory_scope_query
from ananta_contracts.memory_access import (
    MemoryNamespace,
    MemoryScope,
    MemorySecurityMetadata,
    MemorySensitivity,
)

logger = logging.getLogger(__name__)


def _engine():
    from agent.database import engine

    return engine


def _is_missing_memory_entries_table(exc: Exception) -> bool:
    message = str(exc).lower()
    return "memory_entries" in message and ("does not exist" in message or "no such table" in message)


def _is_expired(entry: MemoryEntryDB) -> bool:
    """T026: check if entry has expired via expires_at in memory_metadata."""
    expires_at = (dict(entry.memory_metadata or {})).get("expires_at")
    if expires_at is None:
        return False
    try:
        return float(expires_at) < time.time()
    except (TypeError, ValueError):
        return False


def _matches_scope(entry: MemoryEntryDB, scope: Optional[str]) -> bool:
    """T023: filter by memory_scope stored in memory_metadata."""
    if scope is None:
        return True
    entry_scope = str((dict(entry.memory_metadata or {})).get("memory_scope") or "task")
    return entry_scope == str(scope).strip().lower()


class MemoryEntryRepository:
    def get_by_namespace(
        self, namespace: MemoryNamespace, scope: MemoryScope, *,
        sensitivity_ceiling: MemorySensitivity = MemorySensitivity.internal, limit: int = 100,
    ) -> List[MemoryEntryDB]:
        """Strict path: hard SQL namespace predicates, no fallback to legacy rows."""
        namespace.key(scope)
        if not isinstance(sensitivity_ceiling, MemorySensitivity) or type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("memory_query_invalid")
        statement = result_memory_scope_query(namespace, scope, sensitivity_ceiling)
        statement = statement.order_by(MemoryEntryDB.created_at.desc(), MemoryEntryDB.id).limit(limit)
        with Session(_engine()) as session:
            rows = session.exec(statement).all()
        result = []
        for row in rows:
            try:
                MemorySecurityMetadata.from_mapping(row.memory_metadata["memory_security"])
                expiry = row.memory_metadata.get("expires_at")
                if expiry is not None and (type(expiry) not in (int, float) or not math.isfinite(expiry)):
                    continue
            except (TypeError, ValueError):
                continue
            if (not _is_expired(row) and
                    (row.memory_metadata.get("memory_quarantine") or {}).get("state", "active") == "active"):
                result.append(row)
        return result

    def migrate_security_metadata(
        self, *, limit: int = 1000, after_id: str | None = None, dry_run: bool = False,
        batch_id: str | None = None,
        admit_namespaces: bool = False,
    ) -> dict:
        """Internal operator path; batch_id enables durable, conflict-safe rollback."""
        from agent.repositories.memory_migration import MemoryMetadataMigrationRepository

        return MemoryMetadataMigrationRepository(_engine()).migrate(
            limit=limit, after_id=after_id, dry_run=dry_run, batch_id=batch_id,
            admit_namespaces=admit_namespaces,
        )

    def rollback_security_metadata(self, batch_id: str) -> dict:
        """Restore a journaled batch atomically without overwriting newer metadata."""
        from agent.repositories.memory_migration import MemoryMetadataMigrationRepository

        return MemoryMetadataMigrationRepository(_engine()).rollback(batch_id)

    def get_by_id(self, entry_id: str) -> Optional[MemoryEntryDB]:
        with Session(_engine()) as session:
            return session.get(MemoryEntryDB, entry_id)

    def get_by_task(
        self,
        task_id: str,
        *,
        include_expired: bool = False,
        scope: Optional[str] = None,
    ) -> List[MemoryEntryDB]:
        with Session(_engine()) as session:
            statement = select(MemoryEntryDB).where(MemoryEntryDB.task_id == task_id)
            statement = statement.order_by(MemoryEntryDB.created_at.desc())
            try:
                rows = session.exec(statement).all()
            except (ProgrammingError, OperationalError) as exc:
                if _is_missing_memory_entries_table(exc):
                    logger.warning("memory_entries table is missing; returning empty list for task_id=%s", task_id)
                    return []
                raise
        # T026: filter expired in Python (avoids JSON path queries across DBs)
        # T023: scope filter
        result = []
        for row in rows:
            if not include_expired and _is_expired(row):
                continue
            if not _matches_scope(row, scope):
                continue
            result.append(row)
        return result

    def get_by_goal(
        self,
        goal_id: str,
        *,
        include_expired: bool = False,
        scope: Optional[str] = None,
    ) -> List[MemoryEntryDB]:
        with Session(_engine()) as session:
            statement = select(MemoryEntryDB).where(MemoryEntryDB.goal_id == goal_id)
            statement = statement.order_by(MemoryEntryDB.created_at.desc())
            try:
                rows = session.exec(statement).all()
            except (ProgrammingError, OperationalError) as exc:
                if _is_missing_memory_entries_table(exc):
                    logger.warning("memory_entries table is missing; returning empty list for goal_id=%s", goal_id)
                    return []
                raise
        result = []
        for row in rows:
            if not include_expired and _is_expired(row):
                continue
            if not _matches_scope(row, scope):
                continue
            result.append(row)
        return result

    def save(self, entry: MemoryEntryDB) -> MemoryEntryDB:
        with Session(_engine()) as session:
            session.add(entry)
            session.commit()
            session.refresh(entry)
            return entry
