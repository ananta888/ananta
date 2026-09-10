"""Registry persistence borrowed from an existing Hub write transaction.

The caller owns session lifetime, SQLite immediate-write serialization,
commit/rollback and bounded retry after an absent-row integrity conflict.
This adapter neither creates identities nor opens a second transaction.
"""

import sqlalchemy as sa
from sqlmodel import Session

from agent.db_models.evidence_identity import HubRunEvidenceIdentityDB, HubSourceEvidenceIdentityDB
from agent.ports.evidence_identity import RunEvidenceIdentity, SourceEvidenceIdentity
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


class TransactionEvidenceIdentityRepository:
    def __init__(self, session: Session):
        self._session = session

    def register_source(self, identity: SourceEvidenceIdentity) -> SourceEvidenceIdentity:
        existing = self._read(
            HubSourceEvidenceIdentityDB, identity.tenant_id, identity.project_id, identity.source_id,
        )
        if existing is not None:
            return same_source(existing, identity)
        row = source_row(identity)
        self._session.add(row)
        self._session.flush()
        return source_identity(row)

    def get_source(self, *, tenant_id: str, project_id: str, source_id: str) -> SourceEvidenceIdentity | None:
        row = self._read(HubSourceEvidenceIdentityDB, tenant_id, project_id, source_id)
        return source_identity(row) if row is not None else None

    def reserve_run(self, identity: RunEvidenceIdentity) -> RunEvidenceIdentity:
        existing = self._read(HubRunEvidenceIdentityDB, identity.tenant_id, identity.project_id, identity.run_id)
        if existing is not None:
            return same_run(existing, identity)
        for source_id in sorted(identity.source_ids):
            source = self.get_source(tenant_id=identity.tenant_id, project_id=identity.project_id, source_id=source_id)
            if source is None or source.state != "admitted":
                raise EvidenceIdentityPersistenceError("evidence_run_source_identity_unavailable")
        row = run_row(identity)
        self._session.add(row)
        self._session.flush()
        return run_identity(row)

    def get_run(self, *, tenant_id: str, project_id: str, run_id: str) -> RunEvidenceIdentity | None:
        row = self._read(HubRunEvidenceIdentityDB, tenant_id, project_id, run_id)
        return run_identity(row) if row is not None else None

    def complete_run(
        self, *, tenant_id: str, project_id: str, run_id: str, assignment_id: str, dispatch_lease_id: str,
        terminal_state: str, result_digest: str, updated_at_epoch: float,
    ) -> RunEvidenceIdentity:
        row = self._read(HubRunEvidenceIdentityDB, tenant_id, project_id, run_id)
        apply_run_result(
            row, assignment_id=assignment_id, dispatch_lease_id=dispatch_lease_id,
            terminal_state=terminal_state, result_digest=result_digest, updated_at_epoch=updated_at_epoch,
        )
        self._session.flush()
        return run_identity(row)

    def _read(self, model, tenant_id, project_id, identity):
        if not self._session.in_transaction():
            raise EvidenceIdentityPersistenceError("evidence_caller_transaction_required")
        key = model.source_id if model is HubSourceEvidenceIdentityDB else model.run_id
        statement = sa.select(model).where(
            model.tenant_id == tenant_id, model.project_id == project_id, key == identity,
        )
        if self._session.get_bind().dialect.name == "postgresql":
            statement = statement.with_for_update()
        return self._session.execute(statement.execution_options(populate_existing=True)).scalar_one_or_none()
