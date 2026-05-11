from __future__ import annotations

from typing import Any, List, Optional

from sqlmodel import Session, select

from agent.database import engine
from agent.db_models import RepairOutcomeMemoryDB


class RepairOutcomeMemoryRepository:
    def get_by_id(self, entry_id: str) -> Optional[RepairOutcomeMemoryDB]:
        with Session(engine) as session:
            return session.get(RepairOutcomeMemoryDB, entry_id)

    def save(self, entry: RepairOutcomeMemoryDB) -> RepairOutcomeMemoryDB:
        with Session(engine) as session:
            session.add(entry)
            session.commit()
            session.refresh(entry)
            return entry

    def find(
        self,
        *,
        problem_class: str | None = None,
        platform_target: str | None = None,
        outcome_label: str | None = None,
        procedure_id: str | None = None,
        signature_id: str | None = None,
        limit: int = 20,
    ) -> List[RepairOutcomeMemoryDB]:
        with Session(engine) as session:
            statement = select(RepairOutcomeMemoryDB)
            if problem_class:
                statement = statement.where(RepairOutcomeMemoryDB.problem_class == problem_class)
            if outcome_label:
                statement = statement.where(RepairOutcomeMemoryDB.outcome_label == outcome_label)
            if procedure_id:
                statement = statement.where(RepairOutcomeMemoryDB.procedure_id == procedure_id)
            if signature_id:
                statement = statement.where(RepairOutcomeMemoryDB.signature_id == signature_id)
            if platform_target:
                import json
                statement = statement.where(
                    RepairOutcomeMemoryDB.environment_facts["platform_target"].as_string() == platform_target
                )
            statement = statement.order_by(RepairOutcomeMemoryDB.created_at.desc()).limit(limit)
            return session.exec(statement).all()

    def find_all(self, limit: int = 50) -> List[RepairOutcomeMemoryDB]:
        with Session(engine) as session:
            statement = select(RepairOutcomeMemoryDB).order_by(RepairOutcomeMemoryDB.created_at.desc()).limit(limit)
            return session.exec(statement).all()


repair_outcome_memory_repo = RepairOutcomeMemoryRepository()


def get_repair_outcome_memory_repo() -> RepairOutcomeMemoryRepository:
    return repair_outcome_memory_repo
