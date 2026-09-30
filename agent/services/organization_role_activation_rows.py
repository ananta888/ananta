"""Strictly scoped row loaders for the Organization role activation read model.

Every query filters by tenant, project and Organization and re-checks the
scope of each returned row, so a loader never widens the read boundary.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlmodel import Session, select

from agent.db_models import TaskDB, WorkerJobDB, WorkerSlotLeaseDB
from agent.db_models.organizations import (
    OrganizationRelationDB,
    OrganizationRoleAssignmentDB,
    OrganizationRoleSlotDB,
    OrganizationTopologySnapshotDB,
    OrganizationUnitDB,
)


def active_units(
    session: Session,
    *,
    tenant_id: str,
    project_id: str,
    organization_id: str,
) -> list[OrganizationUnitDB]:
    rows = session.exec(
        select(OrganizationUnitDB)
        .where(OrganizationUnitDB.tenant_id == tenant_id)
        .where(OrganizationUnitDB.project_id == project_id)
        .where(OrganizationUnitDB.organization_id == organization_id)
        .where(OrganizationUnitDB.lifecycle == "active")
        .order_by(OrganizationUnitDB.unit_key, OrganizationUnitDB.id)
    ).all()
    return [
        row
        for row in rows
        if row.tenant_id == tenant_id
        and row.project_id == project_id
        and row.organization_id == organization_id
        and row.lifecycle == "active"
    ]


def active_role_slots(
    session: Session,
    *,
    tenant_id: str,
    project_id: str,
    organization_id: str,
) -> list[OrganizationRoleSlotDB]:
    rows = session.exec(
        select(OrganizationRoleSlotDB)
        .where(OrganizationRoleSlotDB.tenant_id == tenant_id)
        .where(OrganizationRoleSlotDB.project_id == project_id)
        .where(OrganizationRoleSlotDB.organization_id == organization_id)
        .where(OrganizationRoleSlotDB.lifecycle == "active")
        .order_by(OrganizationRoleSlotDB.unit_id, OrganizationRoleSlotDB.slot_key)
    ).all()
    return [
        row
        for row in rows
        if row.tenant_id == tenant_id
        and row.project_id == project_id
        and row.organization_id == organization_id
        and row.lifecycle == "active"
    ]


def active_assignments(
    session: Session,
    *,
    tenant_id: str,
    project_id: str,
    organization_id: str,
) -> list[OrganizationRoleAssignmentDB]:
    rows = session.exec(
        select(OrganizationRoleAssignmentDB)
        .where(OrganizationRoleAssignmentDB.tenant_id == tenant_id)
        .where(OrganizationRoleAssignmentDB.project_id == project_id)
        .where(OrganizationRoleAssignmentDB.organization_id == organization_id)
        .where(OrganizationRoleAssignmentDB.lifecycle == "active")
        .order_by(
            OrganizationRoleAssignmentDB.role_slot_id,
            OrganizationRoleAssignmentDB.id,
        )
    ).all()
    return [
        row
        for row in rows
        if row.tenant_id == tenant_id
        and row.project_id == project_id
        and row.organization_id == organization_id
        and row.lifecycle == "active"
    ]


def latest_snapshot(
    session: Session,
    *,
    tenant_id: str,
    project_id: str,
    organization_id: str,
) -> OrganizationTopologySnapshotDB | None:
    row = session.exec(
        select(OrganizationTopologySnapshotDB)
        .where(OrganizationTopologySnapshotDB.tenant_id == tenant_id)
        .where(OrganizationTopologySnapshotDB.project_id == project_id)
        .where(OrganizationTopologySnapshotDB.organization_id == organization_id)
        .order_by(OrganizationTopologySnapshotDB.revision.desc())
    ).first()
    if row is None:
        return None
    if row.tenant_id != tenant_id or row.project_id != project_id or row.organization_id != organization_id:
        return None
    return row


def active_relations(
    session: Session,
    *,
    tenant_id: str,
    project_id: str,
    organization_id: str,
) -> list[OrganizationRelationDB]:
    rows = session.exec(
        select(OrganizationRelationDB)
        .where(OrganizationRelationDB.tenant_id == tenant_id)
        .where(OrganizationRelationDB.project_id == project_id)
        .where(OrganizationRelationDB.organization_id == organization_id)
        .where(OrganizationRelationDB.lifecycle == "active")
        .where(OrganizationRelationDB.handoff_definition_key.is_not(None))
        .where(OrganizationRelationDB.handoff_definition_version.is_not(None))
        .order_by(OrganizationRelationDB.relation_key, OrganizationRelationDB.id)
    ).all()
    return [
        row
        for row in rows
        if row.tenant_id == tenant_id
        and row.project_id == project_id
        and row.organization_id == organization_id
        and row.lifecycle == "active"
        and row.handoff_definition_key
        and row.handoff_definition_version
    ]


def scoped_tasks(
    session: Session,
    *,
    tenant_id: str,
    project_id: str,
    organization_id: str,
) -> list[TaskDB]:
    rows = session.exec(
        select(TaskDB)
        .where(TaskDB.tenant_id == tenant_id)
        .where(TaskDB.project_id == project_id)
        .where(TaskDB.organization_id == organization_id)
        .order_by(TaskDB.id)
    ).all()
    return [
        row
        for row in rows
        if row.tenant_id == tenant_id and row.project_id == project_id and row.organization_id == organization_id
    ]


def task_worker_jobs(
    session: Session,
    *,
    tasks: Sequence[TaskDB],
) -> list[WorkerJobDB]:
    task_ids = sorted({row.id for row in tasks})
    current_job_by_task_id = {
        row.id: str(row.current_worker_job_id) for row in tasks if str(row.current_worker_job_id or "")
    }
    job_ids = sorted(set(current_job_by_task_id.values()))
    if not task_ids or not job_ids:
        return []
    rows = session.exec(
        select(WorkerJobDB)
        .where(WorkerJobDB.id.in_(job_ids))
        .where(WorkerJobDB.parent_task_id.in_(task_ids))
        .order_by(WorkerJobDB.id)
    ).all()
    return [row for row in rows if current_job_by_task_id.get(str(row.parent_task_id or "")) == row.id]


def task_worker_leases(
    session: Session,
    *,
    tasks: Sequence[TaskDB],
    jobs: Sequence[WorkerJobDB],
) -> list[WorkerSlotLeaseDB]:
    task_ids = sorted({row.id for row in tasks})
    job_by_lease_id = {str(row.slot_lease_id): row for row in jobs if str(row.slot_lease_id or "")}
    lease_ids = sorted(job_by_lease_id)
    if not task_ids or not lease_ids:
        return []
    rows = session.exec(
        select(WorkerSlotLeaseDB)
        .where(WorkerSlotLeaseDB.id.in_(lease_ids))
        .where(WorkerSlotLeaseDB.parent_task_id.in_(task_ids))
        .order_by(WorkerSlotLeaseDB.id)
    ).all()
    return [
        row
        for row in rows
        if (job := job_by_lease_id.get(row.id)) is not None
        and str(row.worker_job_id or "") == job.id
        and str(row.parent_task_id or "") == str(job.parent_task_id or "")
    ]


__all__ = [
    "active_assignments",
    "active_relations",
    "active_role_slots",
    "active_units",
    "latest_snapshot",
    "scoped_tasks",
    "task_worker_jobs",
    "task_worker_leases",
]
