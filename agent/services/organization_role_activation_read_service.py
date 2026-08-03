"""Revision-bound, Hub-owned read model for Organization role activation.

Immutable workflow rules stay separate from live execution facts. Runtime
states are projected only from an exact persisted workflow-step binding and
strictly scoped Task, WorkerJob and lease rows; absent or conflicting evidence
remains unknown.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from sqlmodel import Session, select

from agent.db_models import TaskDB, WorkerJobDB, WorkerSlotLeaseDB
from agent.db_models.organizations import (
    OrganizationInstanceDB,
    OrganizationRelationDB,
    OrganizationRoleAssignmentDB,
    OrganizationRoleSlotDB,
    OrganizationTopologySnapshotDB,
    OrganizationUnitDB,
)
from agent.models.organization_models import VersionedDefinitionRef
from agent.repositories.organizations.definitions import SqlOrganizationDefinitionRepository
from agent.services.organization_definition_catalog_service import (
    FileCatalogDefinitionRepositoryAdapter,
    OrganizationDefinitionCatalogService,
)


class OrganizationRoleActivationReadError(ValueError):
    """Stable read-model failure without leaking persistence details."""

    def __init__(self, reason_code: str, *, details: Mapping[str, Any] | None = None) -> None:
        self.reason_code = reason_code
        self.details = dict(details or {})
        super().__init__(reason_code)


class OrganizationRoleActivationReadService:
    """Build a read-only role/workflow activation projection for one aggregate."""

    SCHEMA = "organization_role_activation_map.v1"
    ROUTER_OWNER = "hub"
    WORKFLOW_BINDING_SCHEMA = "organization_workflow_step_binding.v1"

    def __init__(
        self,
        *,
        catalog: OrganizationDefinitionCatalogService,
        session_factory: Callable[[], Session] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._catalog = catalog
        self._session_factory = session_factory or self._default_session
        self._clock = clock or time.time

    @staticmethod
    def _default_session() -> Session:
        from agent.database import engine

        return Session(engine)

    def read(
        self,
        *,
        tenant_id: str,
        project_id: str,
        organization: OrganizationInstanceDB,
    ) -> dict[str, Any]:
        self._ensure_scope(
            tenant_id=tenant_id,
            project_id=project_id,
            organization=organization,
        )
        organization_id = organization.organization_id
        with self._session_factory() as session:
            units = self._active_units(
                session,
                tenant_id=tenant_id,
                project_id=project_id,
                organization_id=organization_id,
            )
            slots = self._active_role_slots(
                session,
                tenant_id=tenant_id,
                project_id=project_id,
                organization_id=organization_id,
            )
            assignments = self._active_assignments(
                session,
                tenant_id=tenant_id,
                project_id=project_id,
                organization_id=organization_id,
            )
            relations = self._active_relations(
                session,
                tenant_id=tenant_id,
                project_id=project_id,
                organization_id=organization_id,
            )
            tasks = self._scoped_tasks(
                session,
                tenant_id=tenant_id,
                project_id=project_id,
                organization_id=organization_id,
            )
            jobs = self._task_worker_jobs(session, tasks=tasks)
            leases = self._task_worker_leases(
                session,
                tasks=tasks,
                jobs=jobs,
            )
            snapshot = self._latest_snapshot(
                session,
                tenant_id=tenant_id,
                project_id=project_id,
                organization_id=organization_id,
            )
            resolver = FileCatalogDefinitionRepositoryAdapter(
                SqlOrganizationDefinitionRepository(session),
                self._catalog,
                session,
            )
            handoff_definitions = self._handoff_definitions(
                resolver=resolver,
                tenant_id=tenant_id,
                project_id=project_id,
                relations=relations,
            )
            teams, edges = self._project_teams(
                resolver=resolver,
                tenant_id=tenant_id,
                project_id=project_id,
                units=[
                    unit
                    for unit in units
                    if unit.unit_kind == "team" and unit.team_blueprint_key and unit.team_blueprint_version
                ],
                slots=slots,
                assignments=assignments,
            )
            runtime_summary = self._apply_runtime_observations(
                teams=teams,
                tasks=tasks,
                jobs=jobs,
                leases=leases,
                organization=organization,
                now=self._clock(),
            )
            edges.extend(
                self._cross_team_artifact_edges(
                    teams=teams,
                    units=units,
                    relations=relations,
                    handoff_definitions=handoff_definitions,
                )
            )
            edges.sort(
                key=lambda edge: (
                    edge["type"],
                    edge["source"]["ref"],
                    edge["target"]["ref"],
                )
            )

        steps = [step for team in teams for step in team["workflow"]["steps"]]
        snapshot_stale = snapshot is None or snapshot.definition_revision != organization.definition_revision
        return {
            "schema": self.SCHEMA,
            "organization_id": organization_id,
            "definition_revision": organization.definition_revision,
            "snapshot_hash": snapshot.snapshot_hash if snapshot is not None else None,
            "snapshot_revision": snapshot.revision if snapshot is not None else None,
            "stale": snapshot_stale,
            "snapshot_reason_code": (
                "organization_role_activation_snapshot_missing"
                if snapshot is None
                else (
                    "organization_role_activation_snapshot_revision_mismatch"
                    if snapshot_stale
                    else "organization_role_activation_snapshot_current"
                )
            ),
            "router_owner": self.ROUTER_OWNER,
            "runtime_observation": runtime_summary["observation"],
            "summary": {
                "active_team_count": len(teams),
                "workflow_step_count": len(steps),
                "edge_count": len(edges),
                "unbound_step_count": sum(step["role_binding"]["state"] != "bound" for step in steps),
                "runtime_bound_step_count": runtime_summary["bound_step_count"],
                "task_ready_step_count": runtime_summary["task_ready_step_count"],
                "hub_routed_step_count": runtime_summary["hub_routed_step_count"],
                "worker_executing_step_count": runtime_summary["worker_executing_step_count"],
            },
            "teams": teams,
            "edges": edges,
        }

    @staticmethod
    def _ensure_scope(
        *,
        tenant_id: str,
        project_id: str,
        organization: OrganizationInstanceDB,
    ) -> None:
        if (
            organization.tenant_id != tenant_id
            or organization.project_id != project_id
            or not organization.organization_id
        ):
            raise OrganizationRoleActivationReadError("organization_role_activation_scope_mismatch")

    @staticmethod
    def _active_units(
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

    @staticmethod
    def _active_role_slots(
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

    @staticmethod
    def _active_assignments(
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

    @staticmethod
    def _latest_snapshot(
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

    @staticmethod
    def _active_relations(
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

    @staticmethod
    def _scoped_tasks(
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

    @staticmethod
    def _task_worker_jobs(
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

    @staticmethod
    def _task_worker_leases(
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

    def _project_teams(
        self,
        *,
        resolver: FileCatalogDefinitionRepositoryAdapter,
        tenant_id: str,
        project_id: str,
        units: Sequence[OrganizationUnitDB],
        slots: Sequence[OrganizationRoleSlotDB],
        assignments: Sequence[OrganizationRoleAssignmentDB],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        units_by_blueprint: dict[str, list[OrganizationUnitDB]] = defaultdict(list)
        for unit in units:
            units_by_blueprint[self._unit_blueprint_ref(unit)].append(unit)
        slots_by_unit: dict[str, list[OrganizationRoleSlotDB]] = defaultdict(list)
        for slot in slots:
            slots_by_unit[slot.unit_id].append(slot)
        assignment_count = Counter(row.role_slot_id for row in assignments)

        teams: list[dict[str, Any]] = []
        edges: list[dict[str, Any]] = []
        for unit in units:
            blueprint_ref = self._unit_blueprint_ref(unit)
            blueprint_row = resolver.get_team_blueprint(
                tenant_id,
                project_id,
                str(unit.team_blueprint_key),
                int(unit.team_blueprint_version or 0),
            )
            if blueprint_row is None:
                raise OrganizationRoleActivationReadError(
                    "organization_role_activation_team_definition_missing",
                    details={"team_blueprint_ref": blueprint_ref},
                )
            team_definition = self._team_definition(blueprint_row)
            workflow_ref = self._definition_ref(
                team_definition.get("workflow_ref") or self._row_workflow_ref(blueprint_row),
                reason_code="organization_role_activation_workflow_reference_invalid",
            )
            workflow_row = resolver.get_workflow(
                tenant_id,
                project_id,
                workflow_ref.key,
                workflow_ref.version,
            )
            if workflow_row is None:
                raise OrganizationRoleActivationReadError(
                    "organization_role_activation_workflow_definition_missing",
                    details={"workflow_ref": workflow_ref.portable_ref()},
                )
            workflow = self._workflow_definition(workflow_row)
            steps, team_edges = self._workflow_steps(
                unit=unit,
                owning_blueprint_ref=blueprint_ref,
                workflow_ref=workflow_ref.portable_ref(),
                workflow=workflow,
                units_by_blueprint=units_by_blueprint,
                slots_by_unit=slots_by_unit,
                assignment_count=assignment_count,
            )
            edges.extend(team_edges)
            teams.append(
                {
                    "team_unit_id": unit.id,
                    "team_unit_key": unit.unit_key,
                    "team_name": unit.name,
                    "team_blueprint_ref": blueprint_ref,
                    "lifecycle": unit.lifecycle,
                    "revision_binding": {
                        "team_blueprint_content_hash": str(getattr(blueprint_row, "content_hash", "") or ""),
                        "workflow_content_hash": str(getattr(workflow_row, "content_hash", "") or ""),
                    },
                    "workflow": {
                        "workflow_ref": workflow_ref.portable_ref(),
                        "mode": str(workflow.get("mode") or ""),
                        "default_failure_policy": str(workflow.get("default_failure_policy") or "block"),
                        "steps": steps,
                    },
                }
            )
        edges.sort(
            key=lambda edge: (
                edge["type"],
                edge["source"]["ref"],
                edge["target"]["ref"],
            )
        )
        return teams, edges

    def _apply_runtime_observations(
        self,
        *,
        teams: Sequence[dict[str, Any]],
        tasks: Sequence[TaskDB],
        jobs: Sequence[WorkerJobDB],
        leases: Sequence[WorkerSlotLeaseDB],
        organization: OrganizationInstanceDB,
        now: float,
    ) -> dict[str, Any]:
        tasks_by_id = {row.id: row for row in tasks}
        jobs_by_id = {row.id: row for row in jobs if str(row.parent_task_id or "") in tasks_by_id}
        leases_by_id = {
            row.id: row
            for row in leases
            if str(row.parent_task_id or "") in tasks_by_id and str(row.worker_job_id or "") in jobs_by_id
        }
        bound_step_count = 0
        task_ready_step_count = 0
        hub_routed_step_count = 0
        worker_executing_step_count = 0
        workflow_step_count = 0
        for team in teams:
            workflow = dict(team["workflow"])
            workflow_ref = str(workflow["workflow_ref"])
            workflow_hash = str(team["revision_binding"]["workflow_content_hash"])
            for step in workflow["steps"]:
                workflow_step_count += 1
                matching = sorted(
                    (
                        task
                        for task in tasks
                        if self._task_has_exact_workflow_binding(
                            task=task,
                            organization=organization,
                            team_unit_id=str(team["team_unit_id"]),
                            workflow_ref=workflow_ref,
                            workflow_content_hash=workflow_hash,
                            step=step,
                        )
                    ),
                    key=lambda row: row.id,
                )
                runtime = self._step_runtime_projection(
                    matching_tasks=matching,
                    tasks_by_id=tasks_by_id,
                    jobs_by_id=jobs_by_id,
                    leases_by_id=leases_by_id,
                    now=now,
                )
                step["activation"]["runtime"] = runtime
                if runtime["binding"]["state"] == "exact":
                    bound_step_count += 1
                if runtime["task_ready"]["state"] == "observed_true":
                    task_ready_step_count += 1
                if runtime["hub_routed"]["state"] == "observed_true":
                    hub_routed_step_count += 1
                if runtime["worker_executing"]["state"] == "observed_true":
                    worker_executing_step_count += 1

        if bound_step_count == 0:
            observation = {
                "state": "not_observed",
                "reason_code": "organization_role_activation_exact_task_binding_missing",
                "task_state_included": False,
            }
        elif bound_step_count < workflow_step_count:
            observation = {
                "state": "partial",
                "reason_code": "organization_role_activation_runtime_partially_observed",
                "task_state_included": True,
            }
        else:
            observation = {
                "state": "observed",
                "reason_code": "organization_role_activation_runtime_observed",
                "task_state_included": True,
            }
        return {
            "observation": observation,
            "bound_step_count": bound_step_count,
            "task_ready_step_count": task_ready_step_count,
            "hub_routed_step_count": hub_routed_step_count,
            "worker_executing_step_count": worker_executing_step_count,
        }

    @classmethod
    def _task_has_exact_workflow_binding(
        cls,
        *,
        task: TaskDB,
        organization: OrganizationInstanceDB,
        team_unit_id: str,
        workflow_ref: str,
        workflow_content_hash: str,
        step: Mapping[str, Any],
    ) -> bool:
        raw = dict(task.worker_execution_context or {}).get("organization_workflow_step_binding")
        if not isinstance(raw, Mapping):
            return False
        binding = dict(raw)
        expected_fields = {
            "schema",
            "organization_id",
            "definition_revision",
            "workflow_ref",
            "workflow_content_hash",
            "step_id",
            "team_unit_id",
            "team_id",
            "role_slot_id",
            "gate",
            "handoff_ref",
            "failure_policy",
        }
        if set(binding) != expected_fields:
            return False
        expected_gate = dict(step["gate"])
        verification_spec = dict(task.verification_spec or {})
        verification_checks = verification_spec.get("acceptance_checks")
        verification_independence = verification_spec.get("independent_principal_required")
        if not isinstance(verification_checks, list) or not isinstance(verification_independence, bool):
            return False
        return (
            binding.get("schema") == cls.WORKFLOW_BINDING_SCHEMA
            and binding.get("organization_id") == organization.organization_id == task.organization_id
            and binding.get("definition_revision") == organization.definition_revision
            and binding.get("workflow_ref") == workflow_ref
            and binding.get("workflow_content_hash") == workflow_content_hash
            and binding.get("step_id") == step["step_id"]
            and binding.get("team_unit_id") == team_unit_id == task.unit_id
            and binding.get("team_id") == task.team_id
            and binding.get("role_slot_id") == task.role_slot_id
            and task.role_slot_id in set(step["role_binding"]["bound_role_slot_ids"])
            and binding.get("gate") == expected_gate
            and binding.get("handoff_ref") == step["handoff_ref"]
            and binding.get("failure_policy") == step["failure_policy"]
            and verification_checks == expected_gate["acceptance_checks"]
            and verification_spec.get("approval_role_ref") == expected_gate["approval_role_ref"]
            and verification_independence == expected_gate["independent_principal_required"]
            and str(verification_spec.get("failure_policy") or "") == step["failure_policy"]
        )

    @classmethod
    def _step_runtime_projection(
        cls,
        *,
        matching_tasks: Sequence[TaskDB],
        tasks_by_id: Mapping[str, TaskDB],
        jobs_by_id: Mapping[str, WorkerJobDB],
        leases_by_id: Mapping[str, WorkerSlotLeaseDB],
        now: float,
    ) -> dict[str, Any]:
        if not matching_tasks:
            unknown = cls._runtime_fact((), reason_prefix="organization_role_activation")
            return {
                "binding": {
                    "state": "unknown",
                    "reason_code": "organization_role_activation_exact_task_binding_missing",
                    "task_ids": [],
                },
                "task_ready": unknown,
                "hub_routed": unknown,
                "worker_executing": unknown,
                "worker_job_count": 0,
                "active_lease_count": 0,
            }

        readiness = [cls._task_ready_fact(task, tasks_by_id=tasks_by_id) for task in matching_tasks]
        routed = [cls._hub_routed_fact(task) for task in matching_tasks]
        executing = [
            cls._worker_executing_fact(
                task,
                jobs_by_id=jobs_by_id,
                leases_by_id=leases_by_id,
                now=now,
            )
            for task in matching_tasks
        ]
        current_jobs = [
            jobs_by_id[job_id]
            for task in matching_tasks
            if (job_id := str(task.current_worker_job_id or "")) in jobs_by_id
            and str(jobs_by_id[job_id].parent_task_id or "") == task.id
        ]
        active_leases = [
            leases_by_id[lease_id]
            for job in current_jobs
            if (lease_id := str(job.slot_lease_id or "")) in leases_by_id
            and cls._lease_is_active(
                leases_by_id[lease_id],
                task_id=str(job.parent_task_id or ""),
                worker_job_id=job.id,
                now=now,
            )
        ]
        return {
            "binding": {
                "state": "exact",
                "reason_code": "organization_role_activation_exact_task_binding_observed",
                "task_ids": [task.id for task in matching_tasks],
            },
            "task_ready": cls._runtime_fact(
                readiness,
                reason_prefix="organization_role_activation_task_ready",
            ),
            "hub_routed": cls._runtime_fact(
                routed,
                reason_prefix="organization_role_activation_hub_routed",
            ),
            "worker_executing": cls._runtime_fact(
                executing,
                reason_prefix="organization_role_activation_worker_executing",
            ),
            "worker_job_count": len({row.id for row in current_jobs}),
            "active_lease_count": len({row.id for row in active_leases}),
        }

    @staticmethod
    def _runtime_fact(
        values: Sequence[str],
        *,
        reason_prefix: str,
    ) -> dict[str, Any]:
        counts = Counter(values)
        if counts["observed_true"]:
            state = "observed_true"
        elif not values or counts["unknown"]:
            state = "unknown"
        else:
            state = "observed_false"
        return {
            "state": state,
            "reason_code": f"{reason_prefix}_{state}",
            "observed_true_count": counts["observed_true"],
            "observed_false_count": counts["observed_false"],
            "unknown_count": counts["unknown"] if values else 1,
        }

    @staticmethod
    def _task_ready_fact(task: TaskDB, *, tasks_by_id: Mapping[str, TaskDB]) -> str:
        dependencies: list[TaskDB] = []
        for dependency_id in list(task.depends_on or []):
            dependency = tasks_by_id.get(str(dependency_id))
            if dependency is None:
                return "unknown"
            dependencies.append(dependency)
        status = str(task.status or "").strip().lower()
        if status in {"todo", "created", "blocked_by_dependency"} and all(
            str(dependency.status or "").strip().lower() == "completed" for dependency in dependencies
        ):
            return "observed_true"
        return "observed_false"

    @staticmethod
    def _hub_routed_fact(task: TaskDB) -> str:
        context = dict(task.worker_execution_context or {})
        raw_dispatch = context.get("planning_dispatch")
        status = str(task.status or "").strip().lower()
        if not isinstance(raw_dispatch, Mapping):
            return "unknown" if status in {"assigned", "in_progress", "delegated"} else "observed_false"
        dispatch = dict(raw_dispatch)
        dispatch_status = str(dispatch.get("status") or "")
        common_string_fields = (
            "dispatch_intent_id",
            "lease_id",
            "track_revision_id",
            "plan_task_id",
        )
        attempt = dispatch.get("attempt")
        if (
            dispatch.get("schema") != "organization_planning_dispatch.v1"
            or dispatch_status not in {"pending_dispatch", "dispatched"}
            or any(not str(dispatch.get(field) or "").strip() for field in common_string_fields)
            or isinstance(attempt, bool)
            or not isinstance(attempt, int)
            or attempt < 1
            or not str(task.assigned_agent_url or "")
        ):
            return "unknown"
        if dispatch_status == "pending_dispatch":
            if task.current_worker_job_id:
                return "unknown"
            return "observed_true" if status == "assigned" else "observed_false"
        if (
            not str(dispatch.get("assignment_id") or "").strip()
            or str(dispatch.get("worker_job_id") or "") != str(task.current_worker_job_id or "")
            or str(dispatch.get("worker_id") or "") != str(task.assigned_agent_url or "")
        ):
            return "unknown"
        return "observed_true" if status in {"in_progress", "delegated"} else "observed_false"

    @classmethod
    def _worker_executing_fact(
        cls,
        task: TaskDB,
        *,
        jobs_by_id: Mapping[str, WorkerJobDB],
        leases_by_id: Mapping[str, WorkerSlotLeaseDB],
        now: float,
    ) -> str:
        task_status = str(task.status or "").strip().lower()
        worker_job_id = str(task.current_worker_job_id or "")
        if not worker_job_id:
            return "unknown" if task_status in {"in_progress", "delegated"} else "observed_false"
        job = jobs_by_id.get(worker_job_id)
        if job is None or str(job.parent_task_id or "") != task.id:
            return "unknown"
        job_status = str(job.status or "").strip().lower()
        if job_status in {"completed", "failed", "cancelled", "timeout", "rejected"}:
            return "observed_false"
        if job_status != "running" or job.started_at is None or job.finished_at is not None:
            return "observed_false" if job_status in {"created", "delegated", "queued"} else "unknown"
        if task_status not in {"in_progress", "delegated"} or str(job.worker_url or "") != str(
            task.assigned_agent_url or ""
        ):
            return "unknown"
        lease_id = str(job.slot_lease_id or "")
        lease = leases_by_id.get(lease_id)
        if lease is None:
            return "unknown"
        return (
            "observed_true"
            if cls._lease_is_active(
                lease,
                task_id=task.id,
                worker_job_id=job.id,
                now=now,
            )
            else "observed_false"
        )

    @staticmethod
    def _lease_is_active(
        lease: WorkerSlotLeaseDB,
        *,
        task_id: str,
        worker_job_id: str,
        now: float,
    ) -> bool:
        return (
            str(lease.status or "") == "active"
            and str(lease.parent_task_id or "") == task_id
            and str(lease.worker_job_id or "") == worker_job_id
            and lease.released_at is None
            and float(lease.deadline_at) > now
        )

    def _workflow_steps(
        self,
        *,
        unit: OrganizationUnitDB,
        owning_blueprint_ref: str,
        workflow_ref: str,
        workflow: Mapping[str, Any],
        units_by_blueprint: Mapping[str, Sequence[OrganizationUnitDB]],
        slots_by_unit: Mapping[str, Sequence[OrganizationRoleSlotDB]],
        assignment_count: Counter[str],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        raw_steps = workflow.get("steps")
        if not isinstance(raw_steps, list) or not raw_steps:
            raise OrganizationRoleActivationReadError(
                "organization_role_activation_workflow_steps_missing",
                details={"workflow_ref": workflow_ref},
            )
        normalized: list[dict[str, Any]] = []
        step_by_id: dict[str, dict[str, Any]] = {}
        for raw_step in raw_steps:
            if not isinstance(raw_step, Mapping):
                raise OrganizationRoleActivationReadError(
                    "organization_role_activation_workflow_step_invalid",
                    details={"workflow_ref": workflow_ref},
                )
            step = self._normalize_step(raw_step, workflow=workflow)
            step_id = step["step_id"]
            if not step_id or step_id in step_by_id:
                raise OrganizationRoleActivationReadError(
                    "organization_role_activation_workflow_step_invalid",
                    details={"workflow_ref": workflow_ref, "step_id": step_id},
                )
            step["step_ref"] = self._step_ref(unit.id, workflow_ref, step_id)
            normalized.append(step)
            step_by_id[step_id] = step

        for step in normalized:
            missing_dependencies = sorted(set(step["depends_on"]) - set(step_by_id))
            if missing_dependencies:
                raise OrganizationRoleActivationReadError(
                    "organization_role_activation_workflow_dependency_missing",
                    details={
                        "workflow_ref": workflow_ref,
                        "step_id": step["step_id"],
                        "depends_on": missing_dependencies,
                    },
                )
            target_resolution = self._target_resolution(
                unit=unit,
                owning_blueprint_ref=owning_blueprint_ref,
                selector=step["target_team_selector"],
                units_by_blueprint=units_by_blueprint,
            )
            step["target_resolution"] = target_resolution
            step["role_binding"] = self._role_binding(
                owner_role_ref=step["owner_role_ref"],
                target_resolution=target_resolution,
                slots_by_unit=slots_by_unit,
                assignment_count=assignment_count,
            )
            dependency_refs = [step_by_id[dependency]["step_ref"] for dependency in step["depends_on"]]
            ancestor_ids = self._ancestor_step_ids(str(step["step_id"]), step_by_id)
            predecessor_outputs = {
                output for ancestor_id in ancestor_ids for output in step_by_id[ancestor_id]["outputs"]
            }
            step["activation"] = {
                "state": "not_observed",
                "reason_code": "organization_role_activation_runtime_not_observed",
                "router_owner": self.ROUTER_OWNER,
                "rule": ("hub_route_on_workflow_start" if not dependency_refs else "hub_route_after_dependencies"),
                "reacts_to": (
                    [
                        {
                            "kind": "hub_workflow_intake",
                            "source_ref": "hub",
                            "source_owner_role_ref": None,
                        }
                    ]
                    if not dependency_refs
                    else [
                        {
                            "kind": "workflow_step_completion",
                            "source_ref": step_by_id[dependency]["step_ref"],
                            "source_owner_role_ref": step_by_id[dependency]["owner_role_ref"],
                        }
                        for dependency in step["depends_on"]
                    ]
                ),
                "external_inputs": sorted(set(step["inputs"]) - predecessor_outputs),
            }

        edges = self._workflow_edges(normalized)
        return normalized, edges

    @staticmethod
    def _ancestor_step_ids(
        step_id: str,
        step_by_id: Mapping[str, Mapping[str, Any]],
    ) -> set[str]:
        ancestors: set[str] = set()
        pending = list(step_by_id[step_id]["depends_on"])
        while pending:
            candidate = str(pending.pop())
            if candidate in ancestors:
                continue
            ancestors.add(candidate)
            pending.extend(step_by_id[candidate]["depends_on"])
        return ancestors

    def _cross_team_artifact_edges(
        self,
        *,
        teams: Sequence[dict[str, Any]],
        units: Sequence[OrganizationUnitDB],
        relations: Sequence[OrganizationRelationDB],
        handoff_definitions: Mapping[str, Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        teams_by_id = {str(team["team_unit_id"]): team for team in teams}
        children: dict[str, list[str]] = defaultdict(list)
        for unit in units:
            if unit.parent_unit_id:
                children[unit.parent_unit_id].append(unit.id)

        def endpoint_team_ids(unit_id: str) -> list[str]:
            found: list[str] = []
            pending = [unit_id]
            seen: set[str] = set()
            while pending:
                candidate = pending.pop()
                if candidate in seen:
                    continue
                seen.add(candidate)
                if candidate in teams_by_id:
                    found.append(candidate)
                pending.extend(children.get(candidate, ()))
            return sorted(found)

        edges_by_id: dict[str, dict[str, Any]] = {}
        sources_by_target_step: dict[str, dict[tuple[Any, ...], dict[str, Any]]] = defaultdict(dict)
        for relation in relations:
            handoff_ref = f"{relation.handoff_definition_key}@{relation.handoff_definition_version}"
            handoff = handoff_definitions[handoff_ref]
            declared_handoff = self._edge(
                edge_type="declares_handoff",
                source_kind=("team_unit" if relation.source_unit_id in teams_by_id else "organization_unit"),
                source_ref=relation.source_unit_id,
                target_kind=("team_unit" if relation.target_unit_id in teams_by_id else "organization_unit"),
                target_ref=relation.target_unit_id,
                reason_code="organization_relation_handoff_declared",
                metadata={
                    "relation_key": relation.relation_key,
                    "handoff_ref": handoff_ref,
                    "dependency_policy": relation.dependency_policy,
                    "required_artifact_kinds": list(handoff["required_artifact_kinds"]),
                    "acceptance_gate_ref": handoff["acceptance_gate_ref"],
                },
            )
            edges_by_id[declared_handoff["edge_id"]] = declared_handoff
            for source_team_id in endpoint_team_ids(relation.source_unit_id):
                source_steps = teams_by_id[source_team_id]["workflow"]["steps"]
                for target_team_id in endpoint_team_ids(relation.target_unit_id):
                    if source_team_id == target_team_id:
                        continue
                    target_steps = teams_by_id[target_team_id]["workflow"]["steps"]
                    for source in source_steps:
                        if source.get("handoff_ref") != handoff_ref:
                            continue
                        for target in target_steps:
                            artifacts = sorted(set(target["activation"]["external_inputs"]) & set(source["outputs"]))
                            if not artifacts:
                                continue
                            source_info = {
                                "artifacts": artifacts,
                                "source_step_ref": str(source["step_ref"]),
                                "source_owner_role_ref": str(source["owner_role_ref"]),
                                "source_team_unit_id": source_team_id,
                                "handoff_ref": handoff_ref,
                                "relation_key": relation.relation_key,
                            }
                            source_identity = (
                                source_info["source_step_ref"],
                                handoff_ref,
                                tuple(artifacts),
                                relation.relation_key,
                            )
                            sources_by_target_step[str(target["step_ref"])][source_identity] = source_info
                            edge = self._edge(
                                edge_type="produces_input",
                                source_kind="workflow_step",
                                source_ref=str(source["step_ref"]),
                                target_kind="workflow_step",
                                target_ref=str(target["step_ref"]),
                                reason_code="organization_cross_team_handoff_input_declared",
                                metadata={
                                    "artifacts": artifacts,
                                    "handoff_ref": handoff_ref,
                                    "relation_key": relation.relation_key,
                                    "source_team_unit_id": source_team_id,
                                    "target_team_unit_id": target_team_id,
                                },
                            )
                            edges_by_id[edge["edge_id"]] = edge

        for team in teams:
            for target in team["workflow"]["steps"]:
                sources = sources_by_target_step.get(str(target["step_ref"]))
                if sources:
                    target["activation"]["declared_input_sources"] = sorted(
                        sources.values(),
                        key=lambda item: (
                            item["source_step_ref"],
                            item["handoff_ref"],
                            item["relation_key"],
                            item["artifacts"],
                        ),
                    )
        return list(edges_by_id.values())

    @staticmethod
    def _handoff_definitions(
        *,
        resolver: FileCatalogDefinitionRepositoryAdapter,
        tenant_id: str,
        project_id: str,
        relations: Sequence[OrganizationRelationDB],
    ) -> dict[str, dict[str, Any]]:
        definitions: dict[str, dict[str, Any]] = {}
        for relation in relations:
            key = str(relation.handoff_definition_key or "")
            version = int(relation.handoff_definition_version or 0)
            portable_ref = f"{key}@{version}"
            if portable_ref in definitions:
                continue
            row = resolver.get_handoff(tenant_id, project_id, key, version)
            if row is None:
                raise OrganizationRoleActivationReadError(
                    "organization_role_activation_handoff_definition_missing",
                    details={"handoff_ref": portable_ref},
                )
            payload = getattr(row, "definition_json", None)
            definition = dict(payload) if isinstance(payload, Mapping) else {}
            required_artifact_kinds = definition.get(
                "required_artifact_kinds",
                getattr(row, "required_artifact_kinds", None),
            )
            acceptance_gate_ref = definition.get(
                "acceptance_gate_ref",
                getattr(row, "acceptance_gate_ref", None),
            )
            if (
                not isinstance(required_artifact_kinds, list)
                or any(not isinstance(value, str) or not value.strip() for value in required_artifact_kinds)
                or not isinstance(acceptance_gate_ref, str)
                or not acceptance_gate_ref.strip()
            ):
                raise OrganizationRoleActivationReadError(
                    "organization_role_activation_handoff_definition_invalid",
                    details={"handoff_ref": portable_ref},
                )
            definitions[portable_ref] = {
                "required_artifact_kinds": list(required_artifact_kinds),
                "acceptance_gate_ref": acceptance_gate_ref,
            }
        return definitions

    @staticmethod
    def _normalize_step(
        raw_step: Mapping[str, Any],
        *,
        workflow: Mapping[str, Any],
    ) -> dict[str, Any]:
        selector = raw_step.get("target_team_selector")
        gate = raw_step.get("gate")
        if not isinstance(selector, Mapping) or not isinstance(gate, Mapping):
            raise OrganizationRoleActivationReadError("organization_role_activation_workflow_step_invalid")
        cardinality = selector.get("cardinality")
        if isinstance(cardinality, bool) or not isinstance(cardinality, int) or cardinality < 1:
            raise OrganizationRoleActivationReadError("organization_role_activation_workflow_step_invalid")
        return {
            "step_id": str(raw_step.get("step_id") or "").strip(),
            "title": str(raw_step.get("title") or "").strip(),
            "task_kind": str(raw_step.get("task_kind") or "").strip(),
            "owner_role_ref": str(raw_step.get("owner_role_ref") or "").strip(),
            "target_team_selector": {
                "team_blueprint_ref": str(selector.get("team_blueprint_ref") or "").strip(),
                "cardinality": cardinality,
                "routing": str(selector.get("routing") or "").strip(),
            },
            "depends_on": _string_list(raw_step.get("depends_on")),
            "inputs": _string_list(raw_step.get("inputs")),
            "outputs": _string_list(raw_step.get("outputs")),
            "gate": {
                "required": bool(gate.get("required")),
                "acceptance_checks": _string_list(gate.get("acceptance_checks")),
                "approval_role_ref": (
                    str(gate.get("approval_role_ref")).strip() if gate.get("approval_role_ref") else None
                ),
                "independent_principal_required": bool(gate.get("independent_principal_required")),
            },
            "failure_policy": str(raw_step.get("failure_policy") or workflow.get("default_failure_policy") or "block"),
            "handoff_ref": (str(raw_step.get("handoff_ref")).strip() if raw_step.get("handoff_ref") else None),
        }

    @staticmethod
    def _target_resolution(
        *,
        unit: OrganizationUnitDB,
        owning_blueprint_ref: str,
        selector: Mapping[str, Any],
        units_by_blueprint: Mapping[str, Sequence[OrganizationUnitDB]],
    ) -> dict[str, Any]:
        target_ref = str(selector.get("team_blueprint_ref") or "")
        cardinality = int(selector.get("cardinality") or 0)
        candidates = sorted(
            units_by_blueprint.get(target_ref, ()),
            key=lambda candidate: (candidate.unit_key, candidate.id),
        )
        candidate_ids = [candidate.id for candidate in candidates]
        if target_ref == owning_blueprint_ref and cardinality == 1:
            selected_ids = [unit.id]
            state = "bound"
            reason_code = "organization_role_activation_owning_team_bound"
        elif len(candidates) == cardinality:
            selected_ids = candidate_ids
            state = "bound"
            reason_code = "organization_role_activation_candidate_set_bound"
        elif len(candidates) >= cardinality:
            selected_ids = []
            state = "hub_selection_required"
            reason_code = "organization_role_activation_hub_selection_required"
        else:
            selected_ids = []
            state = "unsatisfied"
            reason_code = "organization_role_activation_target_cardinality_unsatisfied"
        return {
            "state": state,
            "reason_code": reason_code,
            "router_owner": OrganizationRoleActivationReadService.ROUTER_OWNER,
            "candidate_team_unit_ids": candidate_ids,
            "bound_team_unit_ids": selected_ids,
        }

    @staticmethod
    def _role_binding(
        *,
        owner_role_ref: str,
        target_resolution: Mapping[str, Any],
        slots_by_unit: Mapping[str, Sequence[OrganizationRoleSlotDB]],
        assignment_count: Counter[str],
    ) -> dict[str, Any]:
        candidate_unit_ids = set(target_resolution["candidate_team_unit_ids"])
        bound_unit_ids = set(target_resolution["bound_team_unit_ids"])
        candidate_slots = sorted(
            (
                slot
                for unit_id in candidate_unit_ids
                for slot in slots_by_unit.get(unit_id, ())
                if f"{slot.role_template_key}@{slot.role_template_version}" == owner_role_ref
            ),
            key=lambda slot: (slot.unit_id, slot.slot_key, slot.id),
        )
        bound_slots = [slot for slot in candidate_slots if slot.unit_id in bound_unit_ids]
        if not candidate_slots:
            state = "unavailable"
            reason_code = "organization_role_activation_owner_role_unavailable"
        elif not bound_unit_ids:
            state = "candidate_only"
            reason_code = "organization_role_activation_role_hub_selection_pending"
        elif not bound_slots:
            state = "unavailable"
            reason_code = "organization_role_activation_owner_role_unavailable"
        else:
            state = "bound"
            reason_code = "organization_role_activation_owner_role_bound"
        return {
            "state": state,
            "reason_code": reason_code,
            "owner_role_ref": owner_role_ref,
            "candidate_role_slot_ids": [slot.id for slot in candidate_slots],
            "bound_role_slot_ids": [slot.id for slot in bound_slots],
            "assignment_coverage": _assignment_coverage(
                bound_slots,
                assignment_count=assignment_count,
            ),
        }

    def _workflow_edges(
        self,
        steps: Sequence[Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        edges: list[dict[str, Any]] = []
        by_id = {str(step["step_id"]): step for step in steps}
        for target in steps:
            for dependency in target["depends_on"]:
                source = by_id[str(dependency)]
                edges.append(
                    self._edge(
                        edge_type="unblocks",
                        source_kind="workflow_step",
                        source_ref=str(source["step_ref"]),
                        target_kind="workflow_step",
                        target_ref=str(target["step_ref"]),
                        reason_code="organization_workflow_dependency_declared",
                        metadata={},
                    )
                )
            ancestor_ids = self._ancestor_step_ids(str(target["step_id"]), by_id)
            for source in steps:
                if str(source["step_id"]) not in ancestor_ids:
                    continue
                artifacts = sorted(set(source["outputs"]) & set(target["inputs"]))
                if artifacts:
                    edges.append(
                        self._edge(
                            edge_type="produces_input",
                            source_kind="workflow_step",
                            source_ref=str(source["step_ref"]),
                            target_kind="workflow_step",
                            target_ref=str(target["step_ref"]),
                            reason_code="organization_workflow_artifact_flow_declared",
                            metadata={"artifacts": artifacts},
                        )
                    )
            gate = dict(target["gate"])
            if gate.get("required"):
                approval_role_ref = str(gate.get("approval_role_ref") or "")
                edges.append(
                    self._edge(
                        edge_type="requires_gate",
                        source_kind="workflow_step",
                        source_ref=str(target["step_ref"]),
                        target_kind=("role_template" if approval_role_ref else "hub"),
                        target_ref=(approval_role_ref or "hub"),
                        reason_code="organization_workflow_gate_declared",
                        metadata={
                            "acceptance_checks": list(gate.get("acceptance_checks") or []),
                            "independent_principal_required": bool(gate.get("independent_principal_required")),
                        },
                    )
                )
        return edges

    @staticmethod
    def _edge(
        *,
        edge_type: str,
        source_kind: str,
        source_ref: str,
        target_kind: str,
        target_ref: str,
        reason_code: str,
        metadata: Mapping[str, Any],
    ) -> dict[str, Any]:
        identity = json.dumps(
            [
                edge_type,
                source_kind,
                source_ref,
                target_kind,
                target_ref,
                str(metadata.get("relation_key") or ""),
                str(metadata.get("handoff_ref") or ""),
            ],
            ensure_ascii=True,
            separators=(",", ":"),
        )
        edge_id = f"activation-edge-{hashlib.sha256(identity.encode()).hexdigest()[:20]}"
        return {
            "edge_id": edge_id,
            "type": edge_type,
            "source": {"kind": source_kind, "ref": source_ref},
            "target": {"kind": target_kind, "ref": target_ref},
            "reason_code": reason_code,
            "metadata": dict(metadata),
        }

    @staticmethod
    def _team_definition(row: Any) -> dict[str, Any]:
        value = getattr(row, "definition_json", None)
        if not isinstance(value, Mapping):
            raise OrganizationRoleActivationReadError("organization_role_activation_team_definition_invalid")
        return dict(value)

    @staticmethod
    def _workflow_definition(row: Any) -> dict[str, Any]:
        value = getattr(row, "definition_json", None)
        if isinstance(value, Mapping):
            return dict(value)
        steps = getattr(row, "steps_json", None)
        if not isinstance(steps, list):
            raise OrganizationRoleActivationReadError("organization_role_activation_workflow_definition_invalid")
        return {
            "key": str(getattr(row, "definition_key", "") or ""),
            "version": int(getattr(row, "version", 0) or 0),
            "mode": str(getattr(row, "mode", "") or ""),
            "default_failure_policy": str(getattr(row, "default_failure_policy", "") or ""),
            "steps": list(steps),
        }

    @staticmethod
    def _row_workflow_ref(row: Any) -> str:
        key = str(getattr(row, "workflow_definition_key", "") or "")
        version = int(getattr(row, "workflow_definition_version", 0) or 0)
        return f"{key}@{version}" if key and version else ""

    @staticmethod
    def _definition_ref(value: Any, *, reason_code: str) -> VersionedDefinitionRef:
        try:
            return VersionedDefinitionRef.parse(str(value or ""))
        except ValueError as exc:
            raise OrganizationRoleActivationReadError(reason_code) from exc

    @staticmethod
    def _unit_blueprint_ref(unit: OrganizationUnitDB) -> str:
        return f"{unit.team_blueprint_key}@{unit.team_blueprint_version}"

    @staticmethod
    def _step_ref(unit_id: str, workflow_ref: str, step_id: str) -> str:
        return f"team:{unit_id}/workflow:{workflow_ref}/step:{step_id}"


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _assignment_coverage(
    slots: Sequence[OrganizationRoleSlotDB],
    *,
    assignment_count: Counter[str],
) -> dict[str, Any]:
    required = sum(slot.min_count for slot in slots)
    desired = sum(slot.default_count for slot in slots)
    active = sum(assignment_count[slot.id] for slot in slots)
    if not slots:
        state = "not_bound"
        reason_code = "organization_role_activation_assignment_not_bound"
    elif active >= desired:
        state = "desired_covered"
        reason_code = "organization_role_activation_assignment_desired_covered"
    elif active >= required:
        state = "minimum_covered"
        reason_code = "organization_role_activation_assignment_minimum_covered"
    elif active == 0:
        state = "unassigned"
        reason_code = "organization_role_activation_assignment_unassigned"
    else:
        state = "understaffed"
        reason_code = "organization_role_activation_assignment_understaffed"
    return {
        "state": state,
        "reason_code": reason_code,
        "required_count": required,
        "desired_count": desired,
        "active_count": active,
    }


__all__ = [
    "OrganizationRoleActivationReadError",
    "OrganizationRoleActivationReadService",
]
