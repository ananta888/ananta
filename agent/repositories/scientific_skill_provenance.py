"""Scoped append-only persistence for scientific skill receipts."""

from __future__ import annotations

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from agent.database import engine
from agent.db_models.scientific_skill_provenance import ScientificSkillProvenanceReceiptDB


class ScientificSkillProvenanceRepository:
    def __init__(self, database=engine) -> None:
        self._database = database

    def append(self, row: ScientificSkillProvenanceReceiptDB) -> ScientificSkillProvenanceReceiptDB:
        with Session(self._database) as session:
            existing = session.get(ScientificSkillProvenanceReceiptDB, row.receipt_digest)
            if existing is not None:
                if existing.payload != row.payload or existing.tenant_id != row.tenant_id or existing.project_id != row.project_id:
                    raise ValueError("scientific_skill_receipt_immutable_conflict")
                return existing
            session.add(row)
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                existing = session.get(ScientificSkillProvenanceReceiptDB, row.receipt_digest)
                if existing is None or existing.payload != row.payload:
                    raise
                return existing
            session.refresh(row)
            return row

    def get(self, *, tenant_id: str, project_id: str, receipt_digest: str) -> ScientificSkillProvenanceReceiptDB | None:
        with Session(self._database) as session:
            return session.exec(
                select(ScientificSkillProvenanceReceiptDB).where(
                    ScientificSkillProvenanceReceiptDB.receipt_digest == receipt_digest,
                    ScientificSkillProvenanceReceiptDB.tenant_id == tenant_id,
                    ScientificSkillProvenanceReceiptDB.project_id == project_id,
                )
            ).first()


__all__ = ["ScientificSkillProvenanceRepository"]
