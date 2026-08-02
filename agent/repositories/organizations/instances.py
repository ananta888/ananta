"""Focused repositories for organization instance aggregate children."""

from __future__ import annotations

from typing import Generic, TypeVar

from sqlmodel import Session, select

from agent.db_models.organizations import (
    CrossTeamTaskDependencyDB,
    OrganizationAdminGrantDB,
    OrganizationInstanceDB,
    OrganizationLayoutPreferenceDB,
    OrganizationMembershipDB,
    OrganizationRelationDB,
    OrganizationRoleAssignmentDB,
    OrganizationRoleSlotDB,
    OrganizationTeamLinkDB,
    OrganizationTopologyPatchGrantDB,
    OrganizationTopologySnapshotDB,
    OrganizationUnitDB,
)
from agent.db_models.teams import TeamDB

T = TypeVar("T")


class _ScopedChildRepository(Generic[T]):
    def __init__(self, session: Session, model: type[T]) -> None:
        self._session = session
        self._model = model

    def add(self, row: T) -> T:
        self._session.add(row)
        return row

    def add_many(self, rows: list[T]) -> list[T]:
        self._session.add_all(rows)
        return rows

    def list_for_organization(self, tenant_id: str, project_id: str, organization_id: str) -> list[T]:
        statement = (
            select(self._model)
            .where(self._model.tenant_id == tenant_id)
            .where(self._model.project_id == project_id)
            .where(self._model.organization_id == organization_id)
        )
        return list(self._session.exec(statement).all())

    def get_scoped(
        self,
        tenant_id: str,
        project_id: str,
        organization_id: str,
        row_id: str,
        *,
        for_update: bool = False,
    ) -> T | None:
        primary_key = next(iter(self._model.__table__.primary_key.columns)).name
        statement = (
            select(self._model)
            .where(self._model.tenant_id == tenant_id)
            .where(self._model.project_id == project_id)
            .where(self._model.organization_id == organization_id)
            .where(getattr(self._model, primary_key) == row_id)
        )
        if for_update:
            statement = statement.with_for_update()
        return self._session.exec(statement).first()


class SqlOrganizationInstanceRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, row: OrganizationInstanceDB) -> OrganizationInstanceDB:
        self._session.add(row)
        return row

    def get_scoped(
        self,
        tenant_id: str,
        project_id: str,
        organization_id: str,
        *,
        for_update: bool = False,
    ) -> OrganizationInstanceDB | None:
        statement = (
            select(OrganizationInstanceDB)
            .where(OrganizationInstanceDB.tenant_id == tenant_id)
            .where(OrganizationInstanceDB.project_id == project_id)
            .where(OrganizationInstanceDB.organization_id == organization_id)
        )
        if for_update:
            statement = statement.with_for_update()
        return self._session.exec(statement).first()

    def get_by_idempotency_key(self, tenant_id: str, project_id: str, idempotency_key: str):
        statement = (
            select(OrganizationInstanceDB)
            .where(OrganizationInstanceDB.tenant_id == tenant_id)
            .where(OrganizationInstanceDB.project_id == project_id)
            .where(OrganizationInstanceDB.idempotency_key == idempotency_key)
        )
        return self._session.exec(statement).first()


class SqlOrganizationUnitRepository(_ScopedChildRepository[OrganizationUnitDB]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, OrganizationUnitDB)


class SqlOrganizationTeamLinkRepository(_ScopedChildRepository[OrganizationTeamLinkDB]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, OrganizationTeamLinkDB)


class SqlOrganizationTeamMaterializationRepository:
    """Stages legacy TeamDB rows in the surrounding Organization transaction."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, row: TeamDB) -> TeamDB:
        self._session.add(row)
        return row

    def get(self, team_id: str) -> TeamDB | None:
        return self._session.get(TeamDB, team_id)


class SqlOrganizationRoleSlotRepository(_ScopedChildRepository[OrganizationRoleSlotDB]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, OrganizationRoleSlotDB)


class SqlOrganizationAssignmentRepository(_ScopedChildRepository[OrganizationRoleAssignmentDB]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, OrganizationRoleAssignmentDB)


class SqlOrganizationRelationRepository(_ScopedChildRepository[OrganizationRelationDB]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, OrganizationRelationDB)


class SqlOrganizationMembershipRepository(_ScopedChildRepository[OrganizationMembershipDB]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, OrganizationMembershipDB)


class SqlOrganizationAdminGrantRepository(_ScopedChildRepository[OrganizationAdminGrantDB]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, OrganizationAdminGrantDB)


class SqlOrganizationTopologyPatchGrantRepository(_ScopedChildRepository[OrganizationTopologyPatchGrantDB]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, OrganizationTopologyPatchGrantDB)

    def get_by_issue_idempotency_key(
        self,
        tenant_id: str,
        project_id: str,
        organization_id: str,
        principal_id: str,
        idempotency_key: str,
        *,
        for_update: bool = False,
    ) -> OrganizationTopologyPatchGrantDB | None:
        statement = (
            select(OrganizationTopologyPatchGrantDB)
            .where(OrganizationTopologyPatchGrantDB.tenant_id == tenant_id)
            .where(OrganizationTopologyPatchGrantDB.project_id == project_id)
            .where(OrganizationTopologyPatchGrantDB.organization_id == organization_id)
            .where(OrganizationTopologyPatchGrantDB.principal_id == principal_id)
            .where(OrganizationTopologyPatchGrantDB.issue_idempotency_key == idempotency_key)
        )
        if for_update:
            statement = statement.with_for_update()
        return self._session.exec(statement).first()


class SqlOrganizationLayoutRepository(_ScopedChildRepository[OrganizationLayoutPreferenceDB]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, OrganizationLayoutPreferenceDB)


class SqlOrganizationSnapshotRepository(_ScopedChildRepository[OrganizationTopologySnapshotDB]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, OrganizationTopologySnapshotDB)

    def latest(self, tenant_id: str, project_id: str, organization_id: str):
        statement = (
            select(OrganizationTopologySnapshotDB)
            .where(OrganizationTopologySnapshotDB.tenant_id == tenant_id)
            .where(OrganizationTopologySnapshotDB.project_id == project_id)
            .where(OrganizationTopologySnapshotDB.organization_id == organization_id)
            .order_by(OrganizationTopologySnapshotDB.revision.desc())
        )
        return self._session.exec(statement).first()


class SqlCrossTeamDependencyRepository(_ScopedChildRepository[CrossTeamTaskDependencyDB]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, CrossTeamTaskDependencyDB)


__all__ = [
    name for name in globals() if name.startswith("SqlOrganization") or name == "SqlCrossTeamDependencyRepository"
]
