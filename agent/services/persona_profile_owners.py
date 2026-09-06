"""Read-only mapping from real organization topology to presentation owners."""

from sqlmodel import select

from agent.db_models import OrganizationInstanceDB, OrganizationRoleAssignmentDB, OrganizationTeamLinkDB


class SqlPersonaProfileOwners:
    def __init__(self, session_factory):
        self.session_factory = session_factory

    def require(self, tenant, project, organization, kind, owner, *, mutable):
        with self.session_factory() as session:
            org = session.exec(
                select(OrganizationInstanceDB).where(
                    OrganizationInstanceDB.tenant_id == tenant,
                    OrganizationInstanceDB.project_id == project,
                    OrganizationInstanceDB.organization_id == organization,
                )
            ).one_or_none()
            if org is None or org.archived_at is not None or org.lifecycle == "archived":
                raise PermissionError("persona_owner_unavailable")
            if mutable and org.lifecycle == "completed":
                raise PermissionError("persona_owner_retired")
            if kind == "organization" and owner == organization:
                return
            if kind == "team":
                # The v1 profile key has no organization column. A shared team
                # must not give one organization's admin control over another.
                links = session.exec(
                    select(OrganizationTeamLinkDB)
                    .where(
                        OrganizationTeamLinkDB.tenant_id == tenant,
                        OrganizationTeamLinkDB.project_id == project,
                        OrganizationTeamLinkDB.team_id == owner,
                        OrganizationTeamLinkDB.lifecycle != "archived",
                    )
                    .limit(2)
                ).all()
                if len(links) == 1 and links[0].organization_id == organization and links[0].archived_at is None:
                    return
            if kind == "agent":
                assignment = session.exec(
                    select(OrganizationRoleAssignmentDB).where(
                        OrganizationRoleAssignmentDB.tenant_id == tenant,
                        OrganizationRoleAssignmentDB.project_id == project,
                        OrganizationRoleAssignmentDB.organization_id == organization,
                        OrganizationRoleAssignmentDB.id == owner,
                        OrganizationRoleAssignmentDB.lifecycle.in_(("proposed", "active", "suspended")),
                    )
                ).one_or_none()
                if assignment is not None and assignment.ended_at is None:
                    return
        raise PermissionError("persona_owner_unavailable")
