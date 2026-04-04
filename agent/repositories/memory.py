import logging
from typing import List

from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlmodel import Session, select

from agent.database import engine
from agent.db_models import MemoryEntryDB

logger = logging.getLogger(__name__)


def _is_missing_memory_entries_table(exc: Exception) -> bool:
    message = str(exc).lower()
    return "memory_entries" in message and ("does not exist" in message or "no such table" in message)


class MemoryEntryRepository:
    def get_by_id(self, entry_id: str):
        with Session(engine) as session:
            return session.get(MemoryEntryDB, entry_id)

    def get_by_task(self, task_id: str) -> List[MemoryEntryDB]:
        with Session(engine) as session:
            statement = select(MemoryEntryDB).where(MemoryEntryDB.task_id == task_id).order_by(MemoryEntryDB.created_at.desc())
            try:
                return session.exec(statement).all()
            except (ProgrammingError, OperationalError) as exc:
                if _is_missing_memory_entries_table(exc):
                    logger.warning("memory_entries table is missing; returning empty memory list for task_id=%s", task_id)
                    return []
                raise

    def get_by_goal(self, goal_id: str) -> List[MemoryEntryDB]:
        with Session(engine) as session:
            statement = select(MemoryEntryDB).where(MemoryEntryDB.goal_id == goal_id).order_by(MemoryEntryDB.created_at.desc())
            try:
                return session.exec(statement).all()
            except (ProgrammingError, OperationalError) as exc:
                if _is_missing_memory_entries_table(exc):
                    logger.warning("memory_entries table is missing; returning empty memory list for goal_id=%s", goal_id)
                    return []
                raise

    def save(self, entry: MemoryEntryDB):
        with Session(engine) as session:
            session.add(entry)
            session.commit()
            session.refresh(entry)
            return entry
