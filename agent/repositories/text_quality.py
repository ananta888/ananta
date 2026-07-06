from __future__ import annotations

import time

from sqlmodel import Session, select

from agent.database import engine
from agent.db_models import TextQualityCriteriaSetDB, TextQualityEvaluationDB


class TextQualityCriteriaSetRepository:
    def save(self, row: TextQualityCriteriaSetDB) -> TextQualityCriteriaSetDB:
        with Session(engine) as session:
            existing = session.exec(
                select(TextQualityCriteriaSetDB).where(TextQualityCriteriaSetDB.checksum == row.checksum)
            ).first()
            if existing:
                return existing
            merged = session.merge(row)
            merged.updated_at = time.time()
            session.commit()
            session.refresh(merged)
            return merged

    def get_by_id(self, row_id: str) -> TextQualityCriteriaSetDB | None:
        with Session(engine) as session:
            return session.get(TextQualityCriteriaSetDB, row_id)

    def list(self, limit: int = 100) -> list[TextQualityCriteriaSetDB]:
        with Session(engine) as session:
            return list(
                session.exec(
                    select(TextQualityCriteriaSetDB)
                    .order_by(TextQualityCriteriaSetDB.updated_at.desc())
                    .limit(max(1, min(limit, 1000)))
                ).all()
            )

    def get_active(self, profile_name: str, language: str, content_kind: str) -> TextQualityCriteriaSetDB | None:
        with Session(engine) as session:
            rows = session.exec(
                select(TextQualityCriteriaSetDB).where(
                    TextQualityCriteriaSetDB.profile_name == profile_name,
                    TextQualityCriteriaSetDB.language == language,
                    TextQualityCriteriaSetDB.status == "enabled",
                )
            ).all()
            return next((row for row in rows if content_kind in row.content_kinds), None)

    def set_status(self, row_id: str, status: str) -> TextQualityCriteriaSetDB | None:
        if status not in {"proposed", "enabled", "archived", "rejected"}:
            raise ValueError("invalid_criteria_status")
        with Session(engine) as session:
            row = session.get(TextQualityCriteriaSetDB, row_id)
            if row is None:
                return None
            if status == "enabled":
                active = session.exec(
                    select(TextQualityCriteriaSetDB).where(
                        TextQualityCriteriaSetDB.profile_name == row.profile_name,
                        TextQualityCriteriaSetDB.language == row.language,
                        TextQualityCriteriaSetDB.status == "enabled",
                    )
                ).all()
                for previous in active:
                    if set(previous.content_kinds) & set(row.content_kinds):
                        previous.status = "archived"
                        previous.updated_at = time.time()
            row.status = status
            row.updated_at = time.time()
            session.add(row)
            session.commit()
            session.refresh(row)
            return row


class TextQualityEvaluationRepository:
    def save(self, row: TextQualityEvaluationDB) -> TextQualityEvaluationDB:
        with Session(engine) as session:
            existing = session.exec(
                select(TextQualityEvaluationDB).where(
                    TextQualityEvaluationDB.identity_checksum == row.identity_checksum
                )
            ).first()
            if existing:
                return existing
            merged = session.merge(row)
            session.commit()
            session.refresh(merged)
            return merged

    def get_by_run(self, planning_run_id: str) -> list[TextQualityEvaluationDB]:
        with Session(engine) as session:
            return list(
                session.exec(
                    select(TextQualityEvaluationDB)
                    .where(TextQualityEvaluationDB.planning_run_id == planning_run_id)
                    .order_by(TextQualityEvaluationDB.created_at.desc())
                ).all()
            )
