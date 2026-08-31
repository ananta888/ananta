"""SQL adapter exposing admitted source receipts to controlling imports."""

from __future__ import annotations

from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from agent.database import engine
from agent.db_models.source_admission_receipt import SourceAdmissionReceiptDB


class SqlControllingSourceAdmission:
    def __init__(self, database_engine: Engine = engine) -> None:
        self._engine = database_engine

    def is_admitted(
        self,
        *,
        tenant_id: str,
        project_id: str,
        source_revision_id: str,
        revision_digest: str,
    ) -> bool:
        with Session(self._engine) as session:
            receipt = session.exec(
                select(SourceAdmissionReceiptDB.receipt_id).where(
                    SourceAdmissionReceiptDB.tenant_id == tenant_id,
                    SourceAdmissionReceiptDB.project_id == project_id,
                    SourceAdmissionReceiptDB.source_revision_id == source_revision_id,
                    SourceAdmissionReceiptDB.revision_digest == revision_digest,
                    SourceAdmissionReceiptDB.decision_state == "admitted",
                )
            ).first()
        return receipt is not None


__all__ = ["SqlControllingSourceAdmission"]
