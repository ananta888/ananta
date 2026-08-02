from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from sqlmodel import Session, select

from agent.db_models import OrganizationAdminGrantDB, OrganizationMembershipDB


@dataclass(frozen=True, slots=True)
class OrganizationAccessPrincipal:
    principal_id: str
    tenant_id: str
    credential_type: str = "user"
    project_id: str | None = None


class OrganizationMembershipService:
    """Fail-closed tenant/project/organization authorization boundary."""

    def __init__(self, *, session_factory: Callable[[], Session] | None = None) -> None:
        self._session_factory = session_factory or self._default_session

    @staticmethod
    def _default_session() -> Session:
        from agent.database import engine

        return Session(engine)

    def can_view(
        self,
        *,
        principal: OrganizationAccessPrincipal,
        tenant_id: str,
        project_id: str,
        organization_id: str,
    ) -> bool:
        if not self._scope_matches(
            principal=principal, tenant_id=tenant_id, project_id=project_id, organization_id=organization_id
        ):
            return False
        with self._session_factory() as session:
            membership = self._membership(
                session=session,
                principal=principal,
                project_id=project_id,
                organization_id=organization_id,
            )
            return membership is not None

    def can_mutate(
        self,
        *,
        principal: OrganizationAccessPrincipal,
        tenant_id: str,
        project_id: str,
        organization_id: str,
        grant_kind: str,
    ) -> bool:
        if not self._scope_matches(
            principal=principal, tenant_id=tenant_id, project_id=project_id, organization_id=organization_id
        ):
            return False
        with self._session_factory() as session:
            membership = self._membership(
                session=session,
                principal=principal,
                project_id=project_id,
                organization_id=organization_id,
            )
            if membership is None or membership.membership_kind != "organization_admin":
                return False
            now = time.time()
            grants = session.exec(
                select(OrganizationAdminGrantDB).where(
                    OrganizationAdminGrantDB.tenant_id == tenant_id,
                    OrganizationAdminGrantDB.project_id == project_id,
                    OrganizationAdminGrantDB.organization_id == organization_id,
                    OrganizationAdminGrantDB.principal_id == principal.principal_id,
                    OrganizationAdminGrantDB.revoked_at.is_(None),  # type: ignore[union-attr]
                )
            ).all()
            return any(
                (row.expires_at is None or float(row.expires_at) >= now)
                and row.grant_kind in {grant_kind, "organization_admin", "*"}
                for row in grants
            )

    def authorized_organization_ids(
        self,
        *,
        principal: OrganizationAccessPrincipal,
        project_id: str | None = None,
    ) -> frozenset[str]:
        if (
            not principal.principal_id
            or not principal.tenant_id
            or (principal.project_id and project_id and principal.project_id != project_id)
        ):
            return frozenset()
        effective_project_id = principal.project_id or project_id
        now = time.time()
        with self._session_factory() as session:
            statement = select(OrganizationMembershipDB).where(
                OrganizationMembershipDB.tenant_id == principal.tenant_id,
                OrganizationMembershipDB.principal_id == principal.principal_id,
            )
            if effective_project_id:
                statement = statement.where(OrganizationMembershipDB.project_id == str(effective_project_id))
            rows = session.exec(statement).all()
            return frozenset(
                row.organization_id for row in rows if row.expires_at is None or float(row.expires_at) >= now
            )

    @staticmethod
    def _membership(
        *,
        session: Session,
        principal: OrganizationAccessPrincipal,
        project_id: str,
        organization_id: str,
    ) -> OrganizationMembershipDB | None:
        row = session.exec(
            select(OrganizationMembershipDB).where(
                OrganizationMembershipDB.tenant_id == principal.tenant_id,
                OrganizationMembershipDB.project_id == project_id,
                OrganizationMembershipDB.organization_id == organization_id,
                OrganizationMembershipDB.principal_id == principal.principal_id,
            )
        ).one_or_none()
        if row is None or (row.expires_at is not None and float(row.expires_at) < time.time()):
            return None
        return row

    @staticmethod
    def _scope_matches(
        *,
        principal: OrganizationAccessPrincipal,
        tenant_id: str,
        project_id: str,
        organization_id: str,
    ) -> bool:
        return bool(
            principal.principal_id
            and principal.tenant_id
            and project_id
            and organization_id
            and principal.tenant_id == str(tenant_id or "")
            and (principal.project_id is None or principal.project_id == str(project_id or ""))
        )


__all__ = ["OrganizationAccessPrincipal", "OrganizationMembershipService"]
