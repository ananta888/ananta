"""Atomic lifecycle projection for an Organization's materialized topology."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from sqlmodel import Session, select

from agent.db_models import (
    OrganizationRelationDB,
    OrganizationRoleAssignmentDB,
    OrganizationRoleSlotDB,
    OrganizationTeamLinkDB,
    OrganizationUnitDB,
    TeamDB,
)

_ASSIGNMENT_SUSPENDED_BY_KEY = "suspended_by"
_ORGANIZATION_PAUSE_MARKER = "organization_pause"


@dataclass(frozen=True, slots=True)
class OrganizationTopologyActivationResult:
    activated_units: int
    activated_team_links: int
    activated_teams: int
    activated_role_slots: int
    activated_relations: int

    def as_dict(self) -> dict[str, int]:
        return {
            "activated_units": self.activated_units,
            "activated_team_links": self.activated_team_links,
            "activated_teams": self.activated_teams,
            "activated_role_slots": self.activated_role_slots,
            "activated_relations": self.activated_relations,
        }


class OrganizationTopologyLifecyclePort(Protocol):
    def activate_planned(
        self,
        *,
        session: Session,
        tenant_id: str,
        project_id: str,
        organization_id: str,
        now: float,
    ) -> OrganizationTopologyActivationResult: ...

    def project_transition(
        self,
        *,
        session: Session,
        tenant_id: str,
        project_id: str,
        organization_id: str,
        target_state: str,
        now: float,
    ) -> dict[str, int | str]: ...


class SqlOrganizationTopologyLifecycleService:
    """Promote planned topology rows without dispatching or starting Workers."""

    def activate_planned(
        self,
        *,
        session: Session,
        tenant_id: str,
        project_id: str,
        organization_id: str,
        now: float,
    ) -> OrganizationTopologyActivationResult:
        units = session.exec(
            select(OrganizationUnitDB)
            .where(OrganizationUnitDB.tenant_id == tenant_id)
            .where(OrganizationUnitDB.project_id == project_id)
            .where(OrganizationUnitDB.organization_id == organization_id)
            .where(OrganizationUnitDB.lifecycle.in_(("planned", "draining")))
            .with_for_update()
        ).all()
        links = session.exec(
            select(OrganizationTeamLinkDB)
            .where(OrganizationTeamLinkDB.tenant_id == tenant_id)
            .where(OrganizationTeamLinkDB.project_id == project_id)
            .where(OrganizationTeamLinkDB.organization_id == organization_id)
            .where(OrganizationTeamLinkDB.lifecycle.in_(("planned", "draining")))
            .with_for_update()
        ).all()
        role_slots = session.exec(
            select(OrganizationRoleSlotDB)
            .where(OrganizationRoleSlotDB.tenant_id == tenant_id)
            .where(OrganizationRoleSlotDB.project_id == project_id)
            .where(OrganizationRoleSlotDB.organization_id == organization_id)
            .where(OrganizationRoleSlotDB.lifecycle.in_(("planned", "draining")))
            .with_for_update()
        ).all()
        relations = session.exec(
            select(OrganizationRelationDB)
            .where(OrganizationRelationDB.tenant_id == tenant_id)
            .where(OrganizationRelationDB.project_id == project_id)
            .where(OrganizationRelationDB.organization_id == organization_id)
            .where(OrganizationRelationDB.lifecycle.in_(("planned", "draining")))
            .with_for_update()
        ).all()
        team_ids = tuple(sorted({row.team_id for row in links}))
        teams = (
            session.exec(
                select(TeamDB)
                .where(TeamDB.id.in_(team_ids))
                .with_for_update()
            ).all()
            if team_ids
            else []
        )

        for row in units:
            row.lifecycle = "active"
            row.updated_at = now
            session.add(row)
        for row in links:
            row.lifecycle = "active"
            row.activated_at = row.activated_at or now
            session.add(row)
        activated_teams = 0
        for row in teams:
            if not row.is_active:
                activated_teams += 1
                row.is_active = True
                session.add(row)
        for row in role_slots:
            row.lifecycle = "active"
            session.add(row)
        for row in relations:
            row.lifecycle = "active"
            session.add(row)

        return OrganizationTopologyActivationResult(
            activated_units=len(units),
            activated_team_links=len(links),
            activated_teams=activated_teams,
            activated_role_slots=len(role_slots),
            activated_relations=len(relations),
        )

    def project_transition(
        self,
        *,
        session: Session,
        tenant_id: str,
        project_id: str,
        organization_id: str,
        target_state: str,
        now: float,
    ) -> dict[str, int | str]:
        """Project Organization lifecycle into routing topology atomically."""

        target = str(target_state or "").strip().lower()
        if target == "active":
            activation = self.activate_planned(
                session=session,
                tenant_id=tenant_id,
                project_id=project_id,
                organization_id=organization_id,
                now=now,
            ).as_dict()
            suspended_assignments = self._assignments(
                session,
                tenant_id=tenant_id,
                project_id=project_id,
                organization_id=organization_id,
                lifecycles=("suspended",),
            )
            assignments = [
                row
                for row in suspended_assignments
                if dict(row.assignment_metadata or {}).get(
                    _ASSIGNMENT_SUSPENDED_BY_KEY
                )
                == _ORGANIZATION_PAUSE_MARKER
            ]
            for row in assignments:
                row.lifecycle = "active"
                row.ended_at = None
                metadata = dict(row.assignment_metadata or {})
                metadata.pop(_ASSIGNMENT_SUSPENDED_BY_KEY, None)
                row.assignment_metadata = metadata
                session.add(row)
            return {
                "action": "activate",
                **activation,
                "transitioned_assignments": len(assignments),
            }
        if target in {"paused", "completed"}:
            return self._project_inactive(
                session=session,
                tenant_id=tenant_id,
                project_id=project_id,
                organization_id=organization_id,
                lifecycle="draining",
                assignment_lifecycle="suspended",
                assignment_suspension_marker=(
                    _ORGANIZATION_PAUSE_MARKER
                    if target == "paused"
                    else None
                ),
                now=now,
                action="pause" if target == "paused" else "complete",
            )
        if target == "archived":
            return self._project_inactive(
                session=session,
                tenant_id=tenant_id,
                project_id=project_id,
                organization_id=organization_id,
                lifecycle="archived",
                assignment_lifecycle="ended",
                now=now,
                action="archive",
            )
        if target == "validated":
            return self._prepare_activation_candidate(
                session=session,
                tenant_id=tenant_id,
                project_id=project_id,
                organization_id=organization_id,
                now=now,
            )
        return {"action": "none"}

    def _project_inactive(
        self,
        *,
        session: Session,
        tenant_id: str,
        project_id: str,
        organization_id: str,
        lifecycle: str,
        assignment_lifecycle: str,
        now: float,
        action: str,
        assignment_suspension_marker: str | None = None,
    ) -> dict[str, int | str]:
        units, links, slots, relations, teams = self._topology_rows(
            session,
            tenant_id=tenant_id,
            project_id=project_id,
            organization_id=organization_id,
            excluded_lifecycle=lifecycle,
        )
        assignments = self._assignments(
            session,
            tenant_id=tenant_id,
            project_id=project_id,
            organization_id=organization_id,
            lifecycles=("proposed", "active", "suspended"),
        )
        for row in units:
            row.lifecycle = lifecycle
            row.updated_at = now
            session.add(row)
        for row in links:
            row.lifecycle = lifecycle
            row.archived_at = now if lifecycle == "archived" else None
            session.add(row)
        for row in slots:
            row.lifecycle = lifecycle
            session.add(row)
        for row in relations:
            row.lifecycle = lifecycle
            session.add(row)
        for row in teams:
            row.is_active = False
            session.add(row)
        transitioned_assignments = 0
        for row in assignments:
            if assignment_lifecycle == "suspended" and row.lifecycle != "active":
                continue
            row.lifecycle = assignment_lifecycle
            row.ended_at = now if assignment_lifecycle == "ended" else None
            metadata = dict(row.assignment_metadata or {})
            if assignment_lifecycle == "suspended" and assignment_suspension_marker:
                metadata[_ASSIGNMENT_SUSPENDED_BY_KEY] = (
                    assignment_suspension_marker
                )
            elif assignment_lifecycle == "ended":
                metadata.pop(_ASSIGNMENT_SUSPENDED_BY_KEY, None)
            row.assignment_metadata = metadata
            session.add(row)
            transitioned_assignments += 1
        return {
            "action": action,
            "transitioned_units": len(units),
            "transitioned_team_links": len(links),
            "transitioned_teams": len(teams),
            "transitioned_role_slots": len(slots),
            "transitioned_relations": len(relations),
            "transitioned_assignments": transitioned_assignments,
        }

    def _prepare_activation_candidate(
        self,
        *,
        session: Session,
        tenant_id: str,
        project_id: str,
        organization_id: str,
        now: float,
    ) -> dict[str, int | str]:
        units, links, slots, relations, teams = self._topology_rows(
            session,
            tenant_id=tenant_id,
            project_id=project_id,
            organization_id=organization_id,
            included_lifecycles=("archived",),
        )
        for row in units:
            row.lifecycle = "planned"
            row.updated_at = now
            session.add(row)
        for row in links:
            row.lifecycle = "planned"
            row.activated_at = None
            row.archived_at = None
            session.add(row)
        for row in slots:
            row.lifecycle = "planned"
            session.add(row)
        for row in relations:
            row.lifecycle = "planned"
            session.add(row)
        for row in teams:
            row.is_active = False
            session.add(row)
        return {
            "action": "prepare_activation_candidate",
            "transitioned_units": len(units),
            "transitioned_team_links": len(links),
            "transitioned_teams": len(teams),
            "transitioned_role_slots": len(slots),
            "transitioned_relations": len(relations),
            "transitioned_assignments": 0,
        }

    @staticmethod
    def _assignments(
        session: Session,
        *,
        tenant_id: str,
        project_id: str,
        organization_id: str,
        lifecycles: tuple[str, ...],
    ) -> list[OrganizationRoleAssignmentDB]:
        return list(
            session.exec(
                select(OrganizationRoleAssignmentDB)
                .where(OrganizationRoleAssignmentDB.tenant_id == tenant_id)
                .where(OrganizationRoleAssignmentDB.project_id == project_id)
                .where(OrganizationRoleAssignmentDB.organization_id == organization_id)
                .where(OrganizationRoleAssignmentDB.lifecycle.in_(lifecycles))
                .with_for_update()
            ).all()
        )

    @staticmethod
    def _topology_rows(
        session: Session,
        *,
        tenant_id: str,
        project_id: str,
        organization_id: str,
        excluded_lifecycle: str | None = None,
        included_lifecycles: tuple[str, ...] | None = None,
    ) -> tuple[list, list, list, list, list]:
        def scoped(model):
            statement = (
                select(model)
                .where(model.tenant_id == tenant_id)
                .where(model.project_id == project_id)
                .where(model.organization_id == organization_id)
            )
            if included_lifecycles is not None:
                statement = statement.where(
                    model.lifecycle.in_(included_lifecycles)
                )
            elif excluded_lifecycle is not None:
                statement = statement.where(
                    model.lifecycle != excluded_lifecycle
                )
            return statement.with_for_update()

        units = list(session.exec(scoped(OrganizationUnitDB)).all())
        links = list(session.exec(scoped(OrganizationTeamLinkDB)).all())
        slots = list(session.exec(scoped(OrganizationRoleSlotDB)).all())
        relations = list(session.exec(scoped(OrganizationRelationDB)).all())
        team_ids = tuple(sorted({row.team_id for row in links}))
        teams = (
            list(
                session.exec(
                    select(TeamDB)
                    .where(TeamDB.id.in_(team_ids))
                    .with_for_update()
                ).all()
            )
            if team_ids
            else []
        )
        return units, links, slots, relations, teams


__all__ = [
    "OrganizationTopologyActivationResult",
    "OrganizationTopologyLifecyclePort",
    "SqlOrganizationTopologyLifecycleService",
]
