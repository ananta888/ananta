"""Repository for RepairExecutionRecordDB persistence.
DRR-T026: Persist and query repair execution records.
"""
from __future__ import annotations

from typing import Any, List, Optional

from sqlmodel import Session, select

from agent.database import engine
from agent.db_models import RepairExecutionRecordDB


class RepairExecutionRecordRepository:
    def save(self, entry: RepairExecutionRecordDB) -> RepairExecutionRecordDB:
        with Session(engine) as session:
            session.add(entry)
            session.commit()
            session.refresh(entry)
            return entry

    def get_by_id(self, entry_id: str) -> Optional[RepairExecutionRecordDB]:
        with Session(engine) as session:
            return session.get(RepairExecutionRecordDB, entry_id)

    def query_by_problem_class(
        self, problem_class: str, *, limit: int = 20
    ) -> List[RepairExecutionRecordDB]:
        with Session(engine) as session:
            statement = (
                select(RepairExecutionRecordDB)
                .where(RepairExecutionRecordDB.problem_class == problem_class)
                .order_by(RepairExecutionRecordDB.created_at.desc())
                .limit(limit)
            )
            return session.exec(statement).all()

    def query_by_signature_id(
        self, signature_id: str, *, limit: int = 20
    ) -> List[RepairExecutionRecordDB]:
        with Session(engine) as session:
            statement = (
                select(RepairExecutionRecordDB)
                .where(RepairExecutionRecordDB.signature_id == signature_id)
                .order_by(RepairExecutionRecordDB.created_at.desc())
                .limit(limit)
            )
            return session.exec(statement).all()

    def query_by_procedure_id(
        self, procedure_id: str, *, limit: int = 20
    ) -> List[RepairExecutionRecordDB]:
        with Session(engine) as session:
            statement = (
                select(RepairExecutionRecordDB)
                .where(RepairExecutionRecordDB.procedure_id == procedure_id)
                .order_by(RepairExecutionRecordDB.created_at.desc())
                .limit(limit)
            )
            return session.exec(statement).all()

    def recent_by_environment(
        self, environment_facts_hash: str, *, limit: int = 20
    ) -> List[RepairExecutionRecordDB]:
        with Session(engine) as session:
            statement = (
                select(RepairExecutionRecordDB)
                .where(
                    RepairExecutionRecordDB.environment_facts_hash
                    == environment_facts_hash
                )
                .order_by(RepairExecutionRecordDB.created_at.desc())
                .limit(limit)
            )
            return session.exec(statement).all()

    def find_all(self, *, limit: int = 50) -> List[RepairExecutionRecordDB]:
        with Session(engine) as session:
            statement = (
                select(RepairExecutionRecordDB)
                .order_by(RepairExecutionRecordDB.created_at.desc())
                .limit(limit)
            )
            return session.exec(statement).all()


_repair_execution_record_repo = RepairExecutionRecordRepository()


def get_repair_execution_record_repo() -> RepairExecutionRecordRepository:
    return _repair_execution_record_repo
