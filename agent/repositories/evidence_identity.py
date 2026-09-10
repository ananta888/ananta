"""Transactional persistence for Hub-issued evidence identities."""

from __future__ import annotations

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from agent.database import engine
from agent.db_models.evidence_identity import (
    HubRunEvidenceIdentityDB,
    HubSourceEvidenceIdentityDB,
)
from agent.ports.evidence_identity import (
    RunEvidenceIdentity,
    SourceEvidenceIdentity,
)
from agent.repositories.evidence_identity_rows import (
    EvidenceIdentityPersistenceError,
    apply_run_result,
    run_identity,
    run_row,
    same_run,
    same_source,
    source_identity,
    source_row,
)
from agent.services.workflow_runtime.sqlalchemy_support import sqlite_transaction_guard


class SqlEvidenceIdentityRepository:
    def __init__(self, database=engine) -> None:
        self._database = database

    def register_source(
        self, identity: SourceEvidenceIdentity
    ) -> SourceEvidenceIdentity:
        row = self._source_row(identity)
        with sqlite_transaction_guard(self._database), Session(self._database) as session:
            existing = session.get(
                HubSourceEvidenceIdentityDB,
                (identity.tenant_id, identity.project_id, identity.source_id),
            )
            if existing is not None:
                return self._same_source(existing, identity)
            session.add(row)
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                existing = session.exec(
                    select(HubSourceEvidenceIdentityDB).where(
                        HubSourceEvidenceIdentityDB.binding_digest
                        == identity.binding_digest
                    )
                ).first()
                if existing is None:
                    raise
                return self._same_source(existing, identity)
            session.refresh(row)
            return self._source(row)

    def get_source(
        self, *, tenant_id: str, project_id: str, source_id: str
    ) -> SourceEvidenceIdentity | None:
        with sqlite_transaction_guard(self._database), Session(self._database) as session:
            row = session.exec(
                select(HubSourceEvidenceIdentityDB).where(
                    HubSourceEvidenceIdentityDB.source_id == source_id,
                    HubSourceEvidenceIdentityDB.tenant_id == tenant_id,
                    HubSourceEvidenceIdentityDB.project_id == project_id,
                )
            ).first()
            return self._source(row) if row is not None else None

    def reserve_run(self, identity: RunEvidenceIdentity) -> RunEvidenceIdentity:
        row = self._run_row(identity)
        with sqlite_transaction_guard(self._database), Session(self._database) as session:
            existing = session.get(
                HubRunEvidenceIdentityDB,
                (identity.tenant_id, identity.project_id, identity.run_id),
            )
            if existing is not None:
                return self._same_run(existing, identity)
            sources = session.exec(
                select(HubSourceEvidenceIdentityDB).where(
                    HubSourceEvidenceIdentityDB.tenant_id == identity.tenant_id,
                    HubSourceEvidenceIdentityDB.project_id == identity.project_id,
                    HubSourceEvidenceIdentityDB.source_id.in_(identity.source_ids),
                    HubSourceEvidenceIdentityDB.state == "admitted",
                )
            ).all()
            if {value.source_id for value in sources} != set(identity.source_ids):
                raise EvidenceIdentityPersistenceError(
                    "evidence_run_source_identity_unavailable"
                )
            session.add(row)
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                existing = session.exec(
                    select(HubRunEvidenceIdentityDB).where(
                        HubRunEvidenceIdentityDB.reservation_key_digest
                        == identity.reservation_key_digest
                    )
                ).first()
                if existing is None:
                    raise
                return self._same_run(existing, identity)
            session.refresh(row)
            return self._run(row)

    def get_run(
        self, *, tenant_id: str, project_id: str, run_id: str
    ) -> RunEvidenceIdentity | None:
        with sqlite_transaction_guard(self._database), Session(self._database) as session:
            row = session.exec(
                select(HubRunEvidenceIdentityDB).where(
                    HubRunEvidenceIdentityDB.run_id == run_id,
                    HubRunEvidenceIdentityDB.tenant_id == tenant_id,
                    HubRunEvidenceIdentityDB.project_id == project_id,
                )
            ).first()
            return self._run(row) if row is not None else None

    def complete_run(
        self,
        *,
        tenant_id: str,
        project_id: str,
        run_id: str,
        assignment_id: str,
        dispatch_lease_id: str,
        terminal_state: str,
        result_digest: str,
        updated_at_epoch: float,
    ) -> RunEvidenceIdentity:
        with sqlite_transaction_guard(self._database), Session(self._database) as session:
            row = session.exec(
                select(HubRunEvidenceIdentityDB)
                .where(
                    HubRunEvidenceIdentityDB.run_id == run_id,
                    HubRunEvidenceIdentityDB.tenant_id == tenant_id,
                    HubRunEvidenceIdentityDB.project_id == project_id,
                )
                .with_for_update()
            ).first()
            apply_run_result(
                row, assignment_id=assignment_id, dispatch_lease_id=dispatch_lease_id,
                terminal_state=terminal_state, result_digest=result_digest, updated_at_epoch=updated_at_epoch,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return self._run(row)

    # Preserve the existing private helper seam while sharing one projection.
    _same_source = staticmethod(same_source)
    _same_run = staticmethod(same_run)
    _source_row = staticmethod(source_row)
    _run_row = staticmethod(run_row)
    _source = staticmethod(source_identity)
    _run = staticmethod(run_identity)


__all__ = ["EvidenceIdentityPersistenceError", "SqlEvidenceIdentityRepository"]
