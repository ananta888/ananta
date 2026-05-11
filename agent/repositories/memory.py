import logging
import time
from typing import List, Optional

from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlmodel import Session, select

from agent.database import engine
from agent.db_models import MemoryEntryDB

logger = logging.getLogger(__name__)


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
    def get_by_id(self, entry_id: str) -> Optional[MemoryEntryDB]:
        with Session(engine) as session:
            return session.get(MemoryEntryDB, entry_id)

    def get_by_task(
        self,
        task_id: str,
        *,
        include_expired: bool = False,
        scope: Optional[str] = None,
    ) -> List[MemoryEntryDB]:
        with Session(engine) as session:
            statement = select(MemoryEntryDB).where(MemoryEntryDB.task_id == task_id).order_by(MemoryEntryDB.created_at.desc())
            try:
                rows = session.exec(statement).all()
            except (ProgrammingError, OperationalError) as exc:
                if _is_missing_memory_entries_table(exc):
                    logger.warning("memory_entries table is missing; returning empty memory list for task_id=%s", task_id)
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
        with Session(engine) as session:
            statement = select(MemoryEntryDB).where(MemoryEntryDB.goal_id == goal_id).order_by(MemoryEntryDB.created_at.desc())
            try:
                rows = session.exec(statement).all()
            except (ProgrammingError, OperationalError) as exc:
                if _is_missing_memory_entries_table(exc):
                    logger.warning("memory_entries table is missing; returning empty memory list for goal_id=%s", goal_id)
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
        with Session(engine) as session:
            session.add(entry)
            session.commit()
            session.refresh(entry)
            return entry
