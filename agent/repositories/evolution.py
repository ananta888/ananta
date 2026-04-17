from __future__ import annotations

import time
from typing import List

from sqlmodel import Session, select

from agent.database import engine
from agent.db_models import EvolutionProposalDB, EvolutionRunDB


class EvolutionRunRepository:
    def get_by_id(self, run_id: str):
        with Session(engine) as session:
            return session.get(EvolutionRunDB, run_id)

    def get_by_task_id(self, task_id: str, limit: int = 100) -> List[EvolutionRunDB]:
        with Session(engine) as session:
            statement = (
                select(EvolutionRunDB)
                .where(EvolutionRunDB.task_id == task_id)
                .order_by(EvolutionRunDB.created_at.desc())
                .limit(max(1, min(int(limit), 1000)))
            )
            return session.exec(statement).all()

    def save(self, run: EvolutionRunDB):
        run.updated_at = time.time()
        with Session(engine) as session:
            merged = session.merge(run)
            session.commit()
            session.refresh(merged)
            return merged


class EvolutionProposalRepository:
    def get_by_id(self, proposal_id: str):
        with Session(engine) as session:
            return session.get(EvolutionProposalDB, proposal_id)

    def get_by_run_id(self, run_id: str) -> List[EvolutionProposalDB]:
        with Session(engine) as session:
            statement = (
                select(EvolutionProposalDB)
                .where(EvolutionProposalDB.run_id == run_id)
                .order_by(EvolutionProposalDB.created_at.asc())
            )
            return session.exec(statement).all()

    def get_by_task_id(self, task_id: str, limit: int = 100) -> List[EvolutionProposalDB]:
        with Session(engine) as session:
            statement = (
                select(EvolutionProposalDB)
                .where(EvolutionProposalDB.task_id == task_id)
                .order_by(EvolutionProposalDB.created_at.desc())
                .limit(max(1, min(int(limit), 1000)))
            )
            return session.exec(statement).all()

    def save(self, proposal: EvolutionProposalDB):
        proposal.updated_at = time.time()
        with Session(engine) as session:
            merged = session.merge(proposal)
            session.commit()
            session.refresh(merged)
            return merged
