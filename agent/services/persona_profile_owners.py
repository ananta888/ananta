"""Read-only mapping from real organization topology to presentation owners."""

from sqlmodel import select

from agent.db_models import (
    OrganizationInstanceDB,
    OrganizationRoleAssignmentDB,
    OrganizationRoleSlotDB,
    OrganizationTeamLinkDB,
    OrganizationUnitDB,
    TeamDB,
)


class SqlPersonaProfileOwners:
    def __init__(self, session_factory):
        self.session_factory = session_factory

    def require_runtime(self, tenant, project, organization, kind, owner):
        layers, _ = self.lineage(tenant, project, organization, kind, owner)
        with self.session_factory() as session:
            org = session.exec(
                select(OrganizationInstanceDB).where(
                    OrganizationInstanceDB.tenant_id == tenant,
                    OrganizationInstanceDB.project_id == project,
                    OrganizationInstanceDB.organization_id == organization,
                )
            ).one_or_none()
            if org is None or org.lifecycle != "active" or org.archived_at is not None:
                raise PermissionError("persona_runtime_organization_inactive")
            for layer_kind, layer_owner in layers:
                if layer_kind == "team":
                    row = session.exec(
                        select(OrganizationTeamLinkDB, TeamDB)
                        .join(
                            TeamDB,
                            OrganizationTeamLinkDB.team_id == TeamDB.id,
                        )
                        .where(
                            OrganizationTeamLinkDB.tenant_id == tenant,
                            OrganizationTeamLinkDB.project_id == project,
                            OrganizationTeamLinkDB.organization_id == organization,
                            OrganizationTeamLinkDB.team_id == layer_owner,
                        )
                    ).one_or_none()
                    if row is None or row[0].lifecycle != "active" or not row[1].is_active:
                        raise PermissionError("persona_runtime_team_inactive")
                    self._require_unit(session, tenant, project, organization, row[0].unit_id, active=True)
                if layer_kind == "agent":
                    assignment = session.exec(
                        select(OrganizationRoleAssignmentDB).where(
                            OrganizationRoleAssignmentDB.tenant_id == tenant,
                            OrganizationRoleAssignmentDB.project_id == project,
                            OrganizationRoleAssignmentDB.organization_id == organization,
                            OrganizationRoleAssignmentDB.id == layer_owner,
                        )
                    ).one_or_none()
                    if assignment is None or assignment.lifecycle != "active" or assignment.ended_at is not None:
                        raise PermissionError("persona_runtime_assignment_inactive")
                    slot = session.exec(
                        select(OrganizationRoleSlotDB).where(
                            OrganizationRoleSlotDB.tenant_id == tenant,
                            OrganizationRoleSlotDB.project_id == project,
                            OrganizationRoleSlotDB.organization_id == organization,
                            OrganizationRoleSlotDB.id == assignment.role_slot_id,
                        )
                    ).one_or_none()
                    if slot is None or slot.lifecycle != "active":
                        raise PermissionError("persona_runtime_slot_inactive")
                    self._require_unit(session, tenant, project, organization, slot.unit_id, active=True)

    def lineage(self, tenant, project, organization, kind, owner):
        """Preview ancestry and a change token, never a worker/run assignment."""
        self.require(tenant, project, organization, kind, owner, mutable=False)
        layers = [("organization", organization)]
        stamp = []
        with self.session_factory() as session:
            org = session.exec(
                select(OrganizationInstanceDB).where(
                    OrganizationInstanceDB.tenant_id == tenant,
                    OrganizationInstanceDB.project_id == project,
                    OrganizationInstanceDB.organization_id == organization,
                )
            ).one_or_none()
            if org is None:
                raise PermissionError("persona_owner_unavailable")
            stamp.extend((org.lock_version, org.lifecycle, org.archived_at))
            if kind == "team":
                layers.append(("team", owner))
            if kind == "agent":
                row = session.exec(
                    select(OrganizationRoleAssignmentDB, OrganizationRoleSlotDB)
                    .join(
                        OrganizationRoleSlotDB,
                        (OrganizationRoleAssignmentDB.role_slot_id == OrganizationRoleSlotDB.id)
                        & (OrganizationRoleAssignmentDB.tenant_id == OrganizationRoleSlotDB.tenant_id)
                        & (OrganizationRoleAssignmentDB.project_id == OrganizationRoleSlotDB.project_id)
                        & (OrganizationRoleAssignmentDB.organization_id == OrganizationRoleSlotDB.organization_id),
                    )
                    .where(
                        OrganizationRoleAssignmentDB.tenant_id == tenant,
                        OrganizationRoleAssignmentDB.project_id == project,
                        OrganizationRoleAssignmentDB.organization_id == organization,
                        OrganizationRoleAssignmentDB.id == owner,
                    )
                ).one_or_none()
                if row is None or row[1].lifecycle == "archived":
                    raise PermissionError("persona_lineage_unavailable")
                assignment, slot = row
                self._require_unit(session, tenant, project, organization, slot.unit_id)
                stamp.extend(
                    (assignment.role_slot_id, assignment.lifecycle, assignment.ended_at, slot.unit_id, slot.lifecycle)
                )
                link = session.exec(
                    select(OrganizationTeamLinkDB).where(
                        OrganizationTeamLinkDB.tenant_id == tenant,
                        OrganizationTeamLinkDB.project_id == project,
                        OrganizationTeamLinkDB.organization_id == organization,
                        OrganizationTeamLinkDB.unit_id == slot.unit_id,
                    )
                ).one_or_none()
                if link is not None:
                    if link.lifecycle == "archived" or link.archived_at is not None:
                        raise PermissionError("persona_lineage_unavailable")
                    layers.append(("team", link.team_id))
                    stamp.extend((link.id, link.lifecycle))
                layers.append(("agent", owner))
        for layer_kind, layer_owner in layers:
            self.require(tenant, project, organization, layer_kind, layer_owner, mutable=False)
        return tuple(layers), tuple(stamp)

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
                    self._require_unit(session, tenant, project, organization, links[0].unit_id)
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

    @staticmethod
    def _require_unit(session, tenant, project, organization, unit_id, *, active=False):
        unit = session.exec(
            select(OrganizationUnitDB).where(
                OrganizationUnitDB.tenant_id == tenant,
                OrganizationUnitDB.project_id == project,
                OrganizationUnitDB.organization_id == organization,
                OrganizationUnitDB.id == unit_id,
            )
        ).one_or_none()
        if unit is None or unit.lifecycle == "archived" or (active and unit.lifecycle != "active"):
            raise PermissionError("persona_lineage_unavailable")
