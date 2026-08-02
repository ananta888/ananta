"""Idempotency and audit-outbox repositories sharing the aggregate Session."""

from __future__ import annotations

from sqlmodel import Session, select

from agent.db_models.organizations import OrganizationAuditOutboxDB, OrganizationOperationDB


class SqlOrganizationOperationRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, row: OrganizationOperationDB) -> OrganizationOperationDB:
        self._session.add(row)
        return row

    def get_by_idempotency_key(
        self,
        tenant_id: str,
        project_id: str,
        operation_kind: str,
        idempotency_key: str,
        *,
        for_update: bool = False,
    ) -> OrganizationOperationDB | None:
        statement = (
            select(OrganizationOperationDB)
            .where(OrganizationOperationDB.tenant_id == tenant_id)
            .where(OrganizationOperationDB.project_id == project_id)
            .where(OrganizationOperationDB.operation_kind == operation_kind)
            .where(OrganizationOperationDB.idempotency_key == idempotency_key)
        )
        if for_update:
            statement = statement.with_for_update()
        return self._session.exec(statement).first()


class SqlOrganizationAuditOutboxRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, row: OrganizationAuditOutboxDB) -> OrganizationAuditOutboxDB:
        self._session.add(row)
        return row


__all__ = ["SqlOrganizationAuditOutboxRepository", "SqlOrganizationOperationRepository"]
