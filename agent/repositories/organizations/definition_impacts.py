"""Scoped read repository for definition lifecycle and reconcile impacts."""

from __future__ import annotations

from sqlmodel import Session, select

from agent.db_models.organizations import (
    OrganizationInstanceDB,
    OrganizationRoleAssignmentDB,
    OrganizationRoleSlotDB,
    OrganizationTopologySnapshotDB,
    OrganizationUnitDB,
)


class SqlOrganizationDefinitionImpactRepository:
    """Batch runtime facts without exposing assignment principals or payloads."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def list_active_instance_ids(
        self,
        tenant_id: str,
        project_id: str,
        key: str,
        version: int,
        *,
        for_update: bool = False,
    ) -> list[str]:
        statement = (
            select(OrganizationInstanceDB)
            .where(OrganizationInstanceDB.tenant_id == tenant_id)
            .where(OrganizationInstanceDB.project_id == project_id)
            .where(OrganizationInstanceDB.definition_key == key)
            .where(OrganizationInstanceDB.definition_version == version)
            .where(OrganizationInstanceDB.lifecycle != "archived")
            .order_by(OrganizationInstanceDB.organization_id)
        )
        if for_update:
            statement = statement.with_for_update()
        return [row.organization_id for row in self._session.exec(statement).all()]

    def list_snapshot_hashes(
        self,
        tenant_id: str,
        project_id: str,
        key: str,
        version: int,
    ) -> list[str]:
        statement = (
            select(OrganizationTopologySnapshotDB.snapshot_hash)
            .join(
                OrganizationInstanceDB,
                (OrganizationInstanceDB.tenant_id == OrganizationTopologySnapshotDB.tenant_id)
                & (OrganizationInstanceDB.project_id == OrganizationTopologySnapshotDB.project_id)
                & (OrganizationInstanceDB.organization_id == OrganizationTopologySnapshotDB.organization_id),
            )
            .where(OrganizationTopologySnapshotDB.tenant_id == tenant_id)
            .where(OrganizationTopologySnapshotDB.project_id == project_id)
            .where(OrganizationInstanceDB.definition_key == key)
            .where(OrganizationInstanceDB.definition_version == version)
            .where(OrganizationInstanceDB.lifecycle != "archived")
            .order_by(
                OrganizationTopologySnapshotDB.organization_id,
                OrganizationTopologySnapshotDB.revision,
            )
        )
        return [str(value) for value in self._session.exec(statement).all()]

    def list_assignment_links(
        self,
        tenant_id: str,
        project_id: str,
        key: str,
        version: int,
    ) -> list[dict[str, str | None]]:
        statement = (
            select(
                OrganizationRoleAssignmentDB.organization_id,
                OrganizationRoleAssignmentDB.id,
                OrganizationRoleAssignmentDB.lifecycle,
                OrganizationRoleSlotDB.slot_key,
                OrganizationUnitDB.unit_key,
                OrganizationUnitDB.group_key,
                OrganizationUnitDB.team_blueprint_key,
                OrganizationUnitDB.team_blueprint_version,
            )
            .join(
                OrganizationRoleSlotDB,
                (OrganizationRoleSlotDB.tenant_id == OrganizationRoleAssignmentDB.tenant_id)
                & (OrganizationRoleSlotDB.project_id == OrganizationRoleAssignmentDB.project_id)
                & (OrganizationRoleSlotDB.organization_id == OrganizationRoleAssignmentDB.organization_id)
                & (OrganizationRoleSlotDB.id == OrganizationRoleAssignmentDB.role_slot_id),
            )
            .join(
                OrganizationUnitDB,
                (OrganizationUnitDB.tenant_id == OrganizationRoleSlotDB.tenant_id)
                & (OrganizationUnitDB.project_id == OrganizationRoleSlotDB.project_id)
                & (OrganizationUnitDB.organization_id == OrganizationRoleSlotDB.organization_id)
                & (OrganizationUnitDB.id == OrganizationRoleSlotDB.unit_id),
            )
            .join(
                OrganizationInstanceDB,
                (OrganizationInstanceDB.tenant_id == OrganizationRoleAssignmentDB.tenant_id)
                & (OrganizationInstanceDB.project_id == OrganizationRoleAssignmentDB.project_id)
                & (OrganizationInstanceDB.organization_id == OrganizationRoleAssignmentDB.organization_id),
            )
            .where(OrganizationRoleAssignmentDB.tenant_id == tenant_id)
            .where(OrganizationRoleAssignmentDB.project_id == project_id)
            .where(OrganizationRoleAssignmentDB.lifecycle != "ended")
            .where(OrganizationInstanceDB.definition_key == key)
            .where(OrganizationInstanceDB.definition_version == version)
            .where(OrganizationInstanceDB.lifecycle != "archived")
            .order_by(
                OrganizationRoleAssignmentDB.organization_id,
                OrganizationUnitDB.unit_key,
                OrganizationRoleSlotDB.slot_key,
                OrganizationRoleAssignmentDB.id,
            )
        )
        return [
            {
                "organization_id": organization_id,
                "assignment_id": assignment_id,
                "unit_key": unit_key,
                "group_key": group_key,
                "role_slot_key": slot_key,
                "role_definition_key": (
                    f"{team_blueprint_key}@{team_blueprint_version}:{slot_key}"
                    if team_blueprint_key and team_blueprint_version
                    else slot_key
                ),
                "lifecycle": lifecycle,
            }
            for (
                organization_id,
                assignment_id,
                lifecycle,
                slot_key,
                unit_key,
                group_key,
                team_blueprint_key,
                team_blueprint_version,
            ) in self._session.exec(statement).all()
        ]


__all__ = ["SqlOrganizationDefinitionImpactRepository"]
