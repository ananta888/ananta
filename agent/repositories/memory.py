from typing import List

from sqlmodel import Session, select

from agent.database import engine
from agent.db_models import MemoryEntryDB


class MemoryEntryRepository:
    def get_by_id(self, entry_id: str):
        with Session(engine) as session:
            return session.get(MemoryEntryDB, entry_id)

    def get_by_task(self, task_id: str) -> List[MemoryEntryDB]:
        with Session(engine) as session:
            statement = select(MemoryEntryDB).where(MemoryEntryDB.task_id == task_id).order_by(MemoryEntryDB.created_at.desc())
            return session.exec(statement).all()

    def get_by_goal(self, goal_id: str) -> List[MemoryEntryDB]:
        with Session(engine) as session:
            statement = select(MemoryEntryDB).where(MemoryEntryDB.goal_id == goal_id).order_by(MemoryEntryDB.created_at.desc())
            return session.exec(statement).all()

    def save(self, entry: MemoryEntryDB):
        with Session(engine) as session:
            session.add(entry)
            session.commit()
            session.refresh(entry)
            return entry
