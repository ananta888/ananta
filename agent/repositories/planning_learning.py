from __future__ import annotations

import time
from typing import Optional

from sqlmodel import Session, select

from agent.database import engine
from agent.db_models import (
    PlanningEvaluationDB,
    PlanningModelProfileDB,
    PlanningPatternClusterDB,
    PlanningPromptVersionDB,
    PlanningReviewItemDB,
    PlanningRunDB,
    PlanningTemplateCandidateDB,
)


class PlanningRunRepository:
    def get_by_id(self, run_id: str) -> Optional[PlanningRunDB]:
        with Session(engine) as session:
            return session.get(PlanningRunDB, run_id)

    def get_by_goal_id(self, goal_id: str, limit: int = 100) -> list[PlanningRunDB]:
        with Session(engine) as session:
            statement = (
                select(PlanningRunDB)
                .where(PlanningRunDB.goal_id == goal_id)
                .order_by(PlanningRunDB.created_at.desc())
                .limit(max(1, min(int(limit), 1000)))
            )
            return session.exec(statement).all()

    def get_recent(self, limit: int = 100) -> list[PlanningRunDB]:
        with Session(engine) as session:
            statement = select(PlanningRunDB).order_by(PlanningRunDB.created_at.desc()).limit(max(1, min(int(limit), 1000)))
            return session.exec(statement).all()

    def save(self, run: PlanningRunDB) -> PlanningRunDB:
        with Session(engine) as session:
            merged = session.merge(run)
            merged.updated_at = time.time()
            session.commit()
            session.refresh(merged)
            return merged


class PlanningPromptVersionRepository:
    def get_by_id(self, version_id: str) -> Optional[PlanningPromptVersionDB]:
        with Session(engine) as session:
            return session.get(PlanningPromptVersionDB, version_id)

    def get_enabled(self) -> list[PlanningPromptVersionDB]:
        with Session(engine) as session:
            statement = (
                select(PlanningPromptVersionDB)
                .where(PlanningPromptVersionDB.enabled == True)
                .order_by(PlanningPromptVersionDB.updated_at.desc())
            )
            return session.exec(statement).all()

    def save(self, version: PlanningPromptVersionDB) -> PlanningPromptVersionDB:
        with Session(engine) as session:
            merged = session.merge(version)
            merged.updated_at = time.time()
            session.commit()
            session.refresh(merged)
            return merged


class PlanningModelProfileRepository:
    def get_enabled(self) -> list[PlanningModelProfileDB]:
        with Session(engine) as session:
            statement = (
                select(PlanningModelProfileDB)
                .where(PlanningModelProfileDB.enabled == True)
                .order_by(PlanningModelProfileDB.updated_at.desc())
            )
            return session.exec(statement).all()

    def save(self, profile: PlanningModelProfileDB) -> PlanningModelProfileDB:
        with Session(engine) as session:
            merged = session.merge(profile)
            merged.updated_at = time.time()
            session.commit()
            session.refresh(merged)
            return merged


class PlanningEvaluationRepository:
    def get_by_run_id(self, planning_run_id: str) -> Optional[PlanningEvaluationDB]:
        with Session(engine) as session:
            statement = select(PlanningEvaluationDB).where(PlanningEvaluationDB.planning_run_id == planning_run_id)
            return session.exec(statement).first()

    def save(self, evaluation: PlanningEvaluationDB) -> PlanningEvaluationDB:
        with Session(engine) as session:
            merged = session.merge(evaluation)
            merged.updated_at = time.time()
            session.commit()
            session.refresh(merged)
            return merged


class PlanningTemplateCandidateRepository:
    def get_recent(self, limit: int = 100) -> list[PlanningTemplateCandidateDB]:
        with Session(engine) as session:
            statement = (
                select(PlanningTemplateCandidateDB)
                .order_by(PlanningTemplateCandidateDB.created_at.desc())
                .limit(max(1, min(int(limit), 1000)))
            )
            return session.exec(statement).all()

    def save(self, candidate: PlanningTemplateCandidateDB) -> PlanningTemplateCandidateDB:
        with Session(engine) as session:
            merged = session.merge(candidate)
            merged.updated_at = time.time()
            session.commit()
            session.refresh(merged)
            return merged


class PlanningPatternClusterRepository:
    def get_recent(self, limit: int = 100) -> list[PlanningPatternClusterDB]:
        with Session(engine) as session:
            statement = (
                select(PlanningPatternClusterDB)
                .order_by(PlanningPatternClusterDB.created_at.desc())
                .limit(max(1, min(int(limit), 1000)))
            )
            return session.exec(statement).all()

    def save(self, cluster: PlanningPatternClusterDB) -> PlanningPatternClusterDB:
        with Session(engine) as session:
            merged = session.merge(cluster)
            merged.updated_at = time.time()
            session.commit()
            session.refresh(merged)
            return merged


class PlanningReviewItemRepository:
    def get_recent(self, limit: int = 100) -> list[PlanningReviewItemDB]:
        with Session(engine) as session:
            statement = (
                select(PlanningReviewItemDB)
                .order_by(PlanningReviewItemDB.created_at.desc())
                .limit(max(1, min(int(limit), 1000)))
            )
            return session.exec(statement).all()

    def get_open(self, limit: int = 200) -> list[PlanningReviewItemDB]:
        with Session(engine) as session:
            statement = (
                select(PlanningReviewItemDB)
                .where(PlanningReviewItemDB.status == "open")
                .order_by(PlanningReviewItemDB.created_at.desc())
                .limit(max(1, min(int(limit), 1000)))
            )
            return session.exec(statement).all()

    def save(self, item: PlanningReviewItemDB) -> PlanningReviewItemDB:
        with Session(engine) as session:
            merged = session.merge(item)
            merged.updated_at = time.time()
            session.commit()
            session.refresh(merged)
            return merged
