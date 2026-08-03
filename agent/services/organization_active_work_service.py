"""Hub-owned active-work inspection and lifecycle execution.

The lifecycle planner stays pure.  This adapter owns the transactional SQL
side effects required to drain, cancel, or explicitly migrate work before an
organization transition.  Existing task identities are never reused for a
migration: the source task is closed and a deterministic successor records
``source_task_id`` lineage.
"""

from __future__ import annotations

import copy
import time
import uuid
from dataclasses import asdict, dataclass
from typing import Any, Mapping

from sqlmodel import Session, select

from agent.db_models.governance import ApprovalRequestDB
from agent.db_models.organizations import (
    CrossTeamTaskDependencyDB,
    OrganizationInstanceDB,
    OrganizationRoleSlotDB,
    OrganizationTeamLinkDB,
    OrganizationUnitDB,
)
from agent.db_models.tasks import TaskDB
from agent.db_models.workers import WorkerJobDB, WorkerSlotLeaseDB
from agent.services.organization_lifecycle_service import OrganizationActivitySnapshot

_RUNNING_TASK_STATES = frozenset({"assigned", "delegated", "doing", "in_progress", "running"})
_TERMINAL_TASK_STATES = frozenset({"completed", "failed", "cancelled", "archived", "rejected"})
_OPEN_DEPENDENCY_STATES = frozenset({"pending", "blocked", "ready"})
_OPEN_GATE_STATES = frozenset({"open", "pending", "blocked"})


class OrganizationActiveWorkError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class OrganizationMigrationTarget:
    organization_id: str
    unit_id: str
    team_id: str
    role_slot_id: str

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "OrganizationMigrationTarget":
        payload = dict(value or {})
        target = cls(
            organization_id=str(payload.get("organization_id") or "").strip(),
            unit_id=str(payload.get("unit_id") or "").strip(),
            team_id=str(payload.get("team_id") or "").strip(),
            role_slot_id=str(payload.get("role_slot_id") or "").strip(),
        )
        if any(not field for field in asdict(target).values()):
            raise OrganizationActiveWorkError("organization_migration_target_incomplete")
        return target


@dataclass(frozen=True, slots=True)
class OrganizationActiveWorkResult:
    strategy: str
    source_task_ids: tuple[str, ...]
    successor_task_ids: tuple[str, ...]
    released_lease_ids: tuple[str, ...]
    stopped_worker_job_ids: tuple[str, ...]
    resolved_gate_ids: tuple[str, ...]
    resolved_handoff_ids: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class SqlOrganizationActiveWorkService:
    """Inspect and transform active work inside the caller's transaction."""

    def snapshot(
        self,
        *,
        session: Session,
        tenant_id: str,
        project_id: str,
        organization_id: str,
        for_update: bool = False,
    ) -> OrganizationActivitySnapshot:
        tasks = self._tasks(
            session,
            tenant_id=tenant_id,
            project_id=project_id,
            organization_id=organization_id,
            for_update=for_update,
            include_queued=True,
        )
        task_ids = tuple(sorted(row.id for row in tasks))
        leases = self._leases(session, task_ids=task_ids, for_update=for_update)
        approvals = self._approvals(
            session,
            tenant_id=tenant_id,
            project_id=project_id,
            organization_id=organization_id,
            for_update=for_update,
        )
        dependencies = self._dependencies(
            session,
            tenant_id=tenant_id,
            project_id=project_id,
            organization_id=organization_id,
            for_update=for_update,
        )
        task_gate_ids = tuple(
            sorted(row.id for row in tasks if self._verification_gate_open(dict(row.verification_status or {})))
        )
        return OrganizationActivitySnapshot(
            running_task_ids=task_ids,
            active_lease_ids=tuple(sorted(row.id for row in leases)),
            open_gate_ids=tuple(sorted((*task_gate_ids, *(row.id for row in approvals)))),
            open_handoff_ids=tuple(sorted(row.id for row in dependencies)),
        )

    def execute(
        self,
        *,
        session: Session,
        tenant_id: str,
        project_id: str,
        organization_id: str,
        strategy: str,
        operation_key: str,
        principal_id: str,
        migration_target: Mapping[str, Any] | None = None,
        unit_ids: tuple[str, ...] | None = None,
        allow_in_place_migration: bool = False,
        include_queued: bool = False,
        now: float | None = None,
    ) -> OrganizationActiveWorkResult:
        normalized_strategy = str(strategy or "").strip().lower()
        if normalized_strategy not in {"drain", "migrate", "cancel"}:
            raise OrganizationActiveWorkError("organization_active_work_strategy_invalid")
        timestamp = float(now if now is not None else time.time())
        tasks = self._tasks(
            session,
            tenant_id=tenant_id,
            project_id=project_id,
            organization_id=organization_id,
            for_update=True,
            unit_ids=unit_ids,
            include_queued=include_queued,
        )
        task_ids = tuple(sorted(row.id for row in tasks))
        leases = self._leases(session, task_ids=task_ids, for_update=True)
        jobs = self._jobs(session, tasks=tasks, for_update=True)
        approvals = self._approvals(
            session,
            tenant_id=tenant_id,
            project_id=project_id,
            organization_id=organization_id,
            for_update=True,
            task_ids=task_ids if unit_ids is not None else None,
        )
        dependencies = self._dependencies(
            session,
            tenant_id=tenant_id,
            project_id=project_id,
            organization_id=organization_id,
            for_update=True,
            task_ids=task_ids if unit_ids is not None else None,
        )

        target = None
        if normalized_strategy == "migrate":
            if migration_target is not None:
                target = OrganizationMigrationTarget.from_mapping(migration_target)
                self._validate_migration_target(
                    session,
                    tenant_id=tenant_id,
                    project_id=project_id,
                    source_organization_id=organization_id,
                    target=target,
                )
            elif not allow_in_place_migration:
                raise OrganizationActiveWorkError("organization_migration_target_incomplete")

        successor_ids: list[str] = []
        task_gate_ids: list[str] = []
        for task in tasks:
            verification = dict(task.verification_status or {})
            if self._verification_gate_open(verification):
                task_gate_ids.append(task.id)
                task.verification_status = {
                    **verification,
                    "status": "cancelled",
                    "reason_code": f"organization_lifecycle_{normalized_strategy}",
                    "resolved_at": timestamp,
                    "resolved_by": principal_id,
                }
            if target is not None:
                successor = self._migrated_successor(
                    source=task,
                    target=target,
                    operation_key=operation_key,
                    principal_id=principal_id,
                    timestamp=timestamp,
                )
                session.add(successor)
                successor_ids.append(successor.id)
                target_status = "cancelled"
                reason_code = "organization_lifecycle_migrated"
            elif normalized_strategy == "drain":
                target_status = "paused"
                reason_code = "organization_lifecycle_drained"
            elif normalized_strategy == "migrate":
                target_status = "todo"
                reason_code = "organization_topology_reparented_for_reroute"
            else:
                target_status = "cancelled"
                reason_code = "organization_lifecycle_cancelled"
            task.status = target_status
            task.status_reason_code = reason_code
            task.status_reason_details = {
                **dict(task.status_reason_details or {}),
                "organization_id": organization_id,
                "operation_key": operation_key,
                "principal_id": principal_id,
                **({"successor_task_id": successor_ids[-1]} if target is not None else {}),
            }
            task.history = [
                *list(task.history or []),
                {
                    "event": reason_code,
                    "timestamp": timestamp,
                    "principal_id": principal_id,
                    **({"successor_task_id": successor_ids[-1]} if target is not None else {}),
                },
            ]
            task.current_worker_job_id = None
            task.updated_at = timestamp
            session.add(task)

        for lease in leases:
            lease.status = "released"
            lease.reason_code = f"organization_lifecycle_{normalized_strategy}"
            lease.released_at = timestamp
            session.add(lease)
        for job in jobs:
            if str(job.status or "").lower() not in {"completed", "failed", "cancelled"}:
                job.status = "cancelled"
                job.finished_at = timestamp
                job.updated_at = timestamp
                job.job_metadata = {
                    **dict(job.job_metadata or {}),
                    "lifecycle_strategy": normalized_strategy,
                    "organization_id": organization_id,
                    "operation_key": operation_key,
                }
                session.add(job)
        for approval in approvals:
            approval.status = "superseded"
            approval.decided_at = timestamp
            approval.decided_by = principal_id
            approval.decision_reason = f"organization_lifecycle_{normalized_strategy}"
            session.add(approval)
        for dependency in dependencies:
            dependency.status = "cancelled"
            dependency.blocking_reason = f"organization_lifecycle_{normalized_strategy}"
            dependency.updated_at = timestamp
            session.add(dependency)

        return OrganizationActiveWorkResult(
            strategy=normalized_strategy,
            source_task_ids=task_ids,
            successor_task_ids=tuple(successor_ids),
            released_lease_ids=tuple(sorted(row.id for row in leases)),
            stopped_worker_job_ids=tuple(sorted(row.id for row in jobs)),
            resolved_gate_ids=tuple(sorted((*task_gate_ids, *(row.id for row in approvals)))),
            resolved_handoff_ids=tuple(sorted(row.id for row in dependencies)),
        )

    @staticmethod
    def _tasks(
        session: Session,
        *,
        tenant_id: str,
        project_id: str,
        organization_id: str,
        for_update: bool,
        unit_ids: tuple[str, ...] | None = None,
        include_queued: bool = False,
    ) -> list[TaskDB]:
        statement = (
            select(TaskDB)
            .where(TaskDB.tenant_id == tenant_id)
            .where(TaskDB.project_id == project_id)
            .where(TaskDB.organization_id == organization_id)
            .order_by(TaskDB.id)
        )
        statement = (
            statement.where(TaskDB.status.not_in(_TERMINAL_TASK_STATES))
            if include_queued
            else statement.where(TaskDB.status.in_(_RUNNING_TASK_STATES))
        )
        if unit_ids is not None:
            if not unit_ids:
                return []
            statement = statement.where(TaskDB.unit_id.in_(unit_ids))
        if for_update:
            statement = statement.with_for_update()
        return list(session.exec(statement).all())

    @staticmethod
    def _leases(session: Session, *, task_ids: tuple[str, ...], for_update: bool) -> list[WorkerSlotLeaseDB]:
        if not task_ids:
            return []
        statement = (
            select(WorkerSlotLeaseDB)
            .where(WorkerSlotLeaseDB.parent_task_id.in_(task_ids))
            .where(WorkerSlotLeaseDB.status == "active")
            .order_by(WorkerSlotLeaseDB.id)
        )
        if for_update:
            statement = statement.with_for_update()
        return list(session.exec(statement).all())

    @staticmethod
    def _jobs(session: Session, *, tasks: list[TaskDB], for_update: bool) -> list[WorkerJobDB]:
        task_ids = {row.id for row in tasks}
        job_ids = {str(row.current_worker_job_id) for row in tasks if row.current_worker_job_id}
        if not task_ids and not job_ids:
            return []
        statement = select(WorkerJobDB).where(
            (WorkerJobDB.id.in_(job_ids)) | (WorkerJobDB.parent_task_id.in_(task_ids))
        )
        if for_update:
            statement = statement.with_for_update()
        return list(session.exec(statement).all())

    @staticmethod
    def _approvals(
        session: Session,
        *,
        tenant_id: str,
        project_id: str,
        organization_id: str,
        for_update: bool,
        task_ids: tuple[str, ...] | None = None,
    ) -> list[ApprovalRequestDB]:
        statement = (
            select(ApprovalRequestDB)
            .where(ApprovalRequestDB.tenant_id == tenant_id)
            .where(ApprovalRequestDB.project_id == project_id)
            .where(ApprovalRequestDB.organization_id == organization_id)
            .where(ApprovalRequestDB.status == "pending")
            .order_by(ApprovalRequestDB.id)
        )
        if task_ids is not None:
            if not task_ids:
                return []
            statement = statement.where(ApprovalRequestDB.task_id.in_(task_ids))
        if for_update:
            statement = statement.with_for_update()
        return list(session.exec(statement).all())

    @staticmethod
    def _dependencies(
        session: Session,
        *,
        tenant_id: str,
        project_id: str,
        organization_id: str,
        for_update: bool,
        task_ids: tuple[str, ...] | None = None,
    ) -> list[CrossTeamTaskDependencyDB]:
        statement = (
            select(CrossTeamTaskDependencyDB)
            .where(CrossTeamTaskDependencyDB.tenant_id == tenant_id)
            .where(CrossTeamTaskDependencyDB.project_id == project_id)
            .where(CrossTeamTaskDependencyDB.organization_id == organization_id)
            .where(CrossTeamTaskDependencyDB.status.in_(_OPEN_DEPENDENCY_STATES))
            .order_by(CrossTeamTaskDependencyDB.id)
        )
        if task_ids is not None:
            if not task_ids:
                return []
            statement = statement.where(
                (CrossTeamTaskDependencyDB.source_task_id.in_(task_ids))
                | (CrossTeamTaskDependencyDB.target_task_id.in_(task_ids))
            )
        if for_update:
            statement = statement.with_for_update()
        return list(session.exec(statement).all())

    @staticmethod
    def _verification_gate_open(value: Mapping[str, Any]) -> bool:
        return str(value.get("status") or "").lower() in _OPEN_GATE_STATES or bool(value.get("open_gates"))

    @staticmethod
    def _validate_migration_target(
        session: Session,
        *,
        tenant_id: str,
        project_id: str,
        source_organization_id: str,
        target: OrganizationMigrationTarget,
    ) -> None:
        if target.organization_id == source_organization_id:
            raise OrganizationActiveWorkError("organization_migration_target_must_be_successor")
        organization = session.exec(
            select(OrganizationInstanceDB)
            .where(OrganizationInstanceDB.tenant_id == tenant_id)
            .where(OrganizationInstanceDB.project_id == project_id)
            .where(OrganizationInstanceDB.organization_id == target.organization_id)
            .where(OrganizationInstanceDB.lifecycle == "active")
            .with_for_update()
        ).first()
        unit = session.exec(
            select(OrganizationUnitDB)
            .where(OrganizationUnitDB.tenant_id == tenant_id)
            .where(OrganizationUnitDB.project_id == project_id)
            .where(OrganizationUnitDB.organization_id == target.organization_id)
            .where(OrganizationUnitDB.id == target.unit_id)
            .where(OrganizationUnitDB.unit_kind == "team")
            .where(OrganizationUnitDB.lifecycle.in_(("planned", "active")))
            .with_for_update()
        ).first()
        link = session.exec(
            select(OrganizationTeamLinkDB)
            .where(OrganizationTeamLinkDB.tenant_id == tenant_id)
            .where(OrganizationTeamLinkDB.project_id == project_id)
            .where(OrganizationTeamLinkDB.organization_id == target.organization_id)
            .where(OrganizationTeamLinkDB.unit_id == target.unit_id)
            .where(OrganizationTeamLinkDB.team_id == target.team_id)
            .where(OrganizationTeamLinkDB.lifecycle.in_(("planned", "active")))
            .with_for_update()
        ).first()
        slot = session.exec(
            select(OrganizationRoleSlotDB)
            .where(OrganizationRoleSlotDB.tenant_id == tenant_id)
            .where(OrganizationRoleSlotDB.project_id == project_id)
            .where(OrganizationRoleSlotDB.organization_id == target.organization_id)
            .where(OrganizationRoleSlotDB.unit_id == target.unit_id)
            .where(OrganizationRoleSlotDB.id == target.role_slot_id)
            .where(OrganizationRoleSlotDB.lifecycle == "active")
            .with_for_update()
        ).first()
        if organization is None or unit is None or link is None or slot is None:
            raise OrganizationActiveWorkError("organization_migration_target_invalid")

    @staticmethod
    def _migrated_successor(
        *,
        source: TaskDB,
        target: OrganizationMigrationTarget,
        operation_key: str,
        principal_id: str,
        timestamp: float,
    ) -> TaskDB:
        task_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"ananta:organization-migration:{operation_key}:{source.id}"))
        payload = copy.deepcopy(source.model_dump())
        payload.update(
            {
                "id": task_id,
                "organization_id": target.organization_id,
                "unit_id": target.unit_id,
                "team_id": target.team_id,
                "role_slot_id": target.role_slot_id,
                "status": "todo",
                "created_at": timestamp,
                "updated_at": timestamp,
                "assigned_agent_url": None,
                "assigned_role_id": None,
                "callback_url": None,
                "callback_token": None,
                "current_worker_job_id": None,
                "plan_id": None,
                "plan_node_id": None,
                "parent_task_id": source.parent_task_id,
                "source_task_id": source.id,
                "derivation_reason": "organization_lifecycle_migration",
                "derivation_depth": int(source.derivation_depth or 0) + 1,
                "history": [
                    *list(source.history or []),
                    {
                        "event": "organization_lifecycle_migration_successor_created",
                        "timestamp": timestamp,
                        "principal_id": principal_id,
                        "source_task_id": source.id,
                    },
                ],
                "status_reason_code": "organization_lifecycle_migration_successor",
                "status_reason_details": {
                    "source_task_id": source.id,
                    "source_organization_id": source.organization_id,
                    "operation_key": operation_key,
                },
            }
        )
        return TaskDB(**payload)


__all__ = [
    "OrganizationActiveWorkError",
    "OrganizationActiveWorkResult",
    "OrganizationMigrationTarget",
    "SqlOrganizationActiveWorkService",
]
