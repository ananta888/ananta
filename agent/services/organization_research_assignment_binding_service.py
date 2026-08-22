"""Persisted assignment and dispatch-lease guard for Category research."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Mapping
from typing import Any

from sqlmodel import Session, select

from agent.db_models import (
    AgentInfoDB,
    OrganizationInstanceDB,
    OrganizationRoleAssignmentDB,
    OrganizationRoleSlotDB,
    OrganizationTeamLinkDB,
    TaskDB,
    WorkerJobDB,
    WorkerSlotLeaseDB,
)
from agent.services.organization_assignment_eligibility_service import (
    OrganizationAssignmentEligibilityService,
)

_ASSIGNMENT_BINDING_SCHEMA = (
    "organization_category_research_assignment_binding.v1"
)
_ROUTING_SCHEMA = "organization_routing_decision.v1"
_ACTIVE_TASK_STATUSES = ("assigned", "in_progress")
_ACTIVE_JOB_STATUSES = ("delegated", "running")


class OrganizationResearchAssignmentBindingError(RuntimeError):
    """Stable fail-closed assignment/lease error."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = str(reason_code)
        super().__init__(self.reason_code)


class OrganizationResearchAssignmentBindingService:
    """Revalidate the exact Hub-selected Organization assignment."""

    def __init__(
        self,
        *,
        session_factory: Callable[[], Session] | None = None,
        eligibility: OrganizationAssignmentEligibilityService | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._session_factory = session_factory or self._default_session
        self._eligibility = (
            eligibility or OrganizationAssignmentEligibilityService()
        )
        self._clock = clock

    @staticmethod
    def _default_session() -> Session:
        from agent.database import engine

        return Session(engine)

    def resolve_worker(self, task: Mapping[str, Any]) -> str | None:
        """Return the bound Worker only while every assignment fact is current."""

        try:
            return self._verify(task=task, dispatch=None)
        except OrganizationResearchAssignmentBindingError:
            return None

    def verify_dispatch(
        self,
        *,
        task: Mapping[str, Any],
        worker_url: str,
        worker_job_id: str,
        subtask_id: str,
        context_bundle_id: str,
    ) -> None:
        resolved = self._verify(
            task=task,
            dispatch={
                "worker_url": str(worker_url or "").strip(),
                "worker_job_id": str(worker_job_id or "").strip(),
                "subtask_id": str(subtask_id or "").strip(),
                "context_bundle_id": str(context_bundle_id or "").strip(),
            },
        )
        if resolved != str(worker_url or "").strip():
            self._fail("category_research_assignment_worker_changed")

    def _verify(
        self,
        *,
        task: Mapping[str, Any],
        dispatch: Mapping[str, str] | None,
    ) -> str:
        task_id = str(task.get("id") or "").strip()
        tenant_id = str(task.get("tenant_id") or "").strip()
        project_id = str(task.get("project_id") or "").strip()
        organization_id = str(task.get("organization_id") or "").strip()
        if not all((task_id, tenant_id, project_id, organization_id)):
            self._fail("category_research_assignment_scope_invalid")

        with self._session_factory() as session:
            task_statement = select(TaskDB).where(
                TaskDB.id == task_id,
                TaskDB.tenant_id == tenant_id,
                TaskDB.project_id == project_id,
                TaskDB.organization_id == organization_id,
                TaskDB.task_kind == "planning_research",
            )
            if self._supports_row_lock(session):
                task_statement = task_statement.with_for_update()
            authoritative = session.exec(task_statement).one_or_none()
            if authoritative is None:
                self._fail("category_research_assignment_task_not_found")

            worker_context = dict(
                authoritative.worker_execution_context or {}
            )
            binding = self._mapping(
                worker_context.get("planning_research_assignment")
            )
            routing = self._mapping(
                worker_context.get("organization_routing")
            )
            self._verify_persisted_binding(
                authoritative,
                binding=binding,
                routing=routing,
            )
            agent_url = str(binding.get("agent_url") or "")

            organization_statement = select(OrganizationInstanceDB).where(
                OrganizationInstanceDB.tenant_id == tenant_id,
                OrganizationInstanceDB.project_id == project_id,
                OrganizationInstanceDB.organization_id == organization_id,
            )
            team_statement = select(OrganizationTeamLinkDB).where(
                OrganizationTeamLinkDB.tenant_id == tenant_id,
                OrganizationTeamLinkDB.project_id == project_id,
                OrganizationTeamLinkDB.organization_id == organization_id,
                OrganizationTeamLinkDB.unit_id
                == str(authoritative.unit_id or ""),
                OrganizationTeamLinkDB.team_id
                == str(authoritative.team_id or ""),
            )
            slot_statement = select(OrganizationRoleSlotDB).where(
                OrganizationRoleSlotDB.tenant_id == tenant_id,
                OrganizationRoleSlotDB.project_id == project_id,
                OrganizationRoleSlotDB.organization_id == organization_id,
                OrganizationRoleSlotDB.unit_id
                == str(authoritative.unit_id or ""),
                OrganizationRoleSlotDB.id
                == str(authoritative.role_slot_id or ""),
            )
            assignment_statement = select(
                OrganizationRoleAssignmentDB
            ).where(
                OrganizationRoleAssignmentDB.id
                == str(binding.get("assignment_id") or ""),
                OrganizationRoleAssignmentDB.tenant_id == tenant_id,
                OrganizationRoleAssignmentDB.project_id == project_id,
                OrganizationRoleAssignmentDB.organization_id
                == organization_id,
                OrganizationRoleAssignmentDB.role_slot_id
                == str(authoritative.role_slot_id or ""),
                OrganizationRoleAssignmentDB.agent_url == agent_url,
            )
            agent_statement = select(AgentInfoDB).where(
                AgentInfoDB.url == agent_url
            )
            if self._supports_row_lock(session):
                organization_statement = (
                    organization_statement.with_for_update()
                )
                team_statement = team_statement.with_for_update()
                slot_statement = slot_statement.with_for_update()
                assignment_statement = assignment_statement.with_for_update()
                agent_statement = agent_statement.with_for_update()

            organization = session.exec(
                organization_statement
            ).one_or_none()
            team = session.exec(team_statement).one_or_none()
            slot = session.exec(slot_statement).one_or_none()
            assignment = session.exec(
                assignment_statement
            ).one_or_none()
            agent = session.exec(agent_statement).one_or_none()
            if (
                organization is None
                or str(organization.lifecycle or "") != "active"
            ):
                self._fail("category_research_organization_not_active")
            if str(organization.effective_limit_profile_hash or "") != str(
                binding.get("effective_policy_hash") or ""
            ):
                self._fail("category_research_assignment_policy_changed")
            if team is None or str(team.lifecycle or "") != "active":
                self._fail("category_research_team_not_active")
            if slot is None or str(slot.lifecycle or "") != "active":
                self._fail("category_research_role_slot_not_active")
            if (
                assignment is None
                or str(assignment.lifecycle or "") != "active"
            ):
                self._fail("category_research_assignment_not_active")

            active_tasks = list(
                session.exec(
                    select(TaskDB).where(
                        TaskDB.assigned_agent_url == agent_url,
                        TaskDB.status.in_(_ACTIVE_TASK_STATUSES),
                        TaskDB.id != authoritative.id,
                    )
                ).all()
            )
            referenced_job_ids = {
                str(row.current_worker_job_id or "")
                for row in active_tasks
                if str(row.current_worker_job_id or "")
            }
            slot_policy = dict(slot.assignment_policy or {})
            required_capabilities = {
                str(value).strip()
                for value in list(
                    authoritative.required_capabilities or []
                )
                if str(value).strip()
            } | {
                str(value).strip()
                for value in list(
                    slot_policy.get("required_capabilities") or []
                )
                if str(value).strip()
            }
            if sorted(required_capabilities) != sorted(
                str(value)
                for value in list(
                    binding.get("required_capabilities") or []
                )
            ):
                self._fail(
                    "category_research_assignment_capabilities_changed"
                )
            eligibility = self._eligibility.evaluate(
                agent=agent,
                required_capabilities=required_capabilities,
                forbidden_capabilities={
                    str(value).strip()
                    for value in list(
                        slot_policy.get("forbidden_capabilities") or []
                    )
                    if str(value).strip()
                },
                capacity_used=len(active_tasks),
                principal_kind_allowed="agent"
                in {
                    str(value).strip()
                    for value in list(
                        slot_policy.get("principal_kinds") or []
                    )
                },
                write_access_required=bool(
                    slot_policy.get("write_access_required", False)
                ),
            )
            if not eligibility.allowed:
                self._fail("category_research_assignment_ineligible")

            if dispatch is not None:
                self._verify_worker_job(
                    session,
                    task=authoritative,
                    dispatch=dispatch,
                    agent_url=agent_url,
                    capacity_limit=eligibility.capacity_limit,
                    active_task_count=len(active_tasks),
                    referenced_job_ids=referenced_job_ids,
                )
            return agent_url

    def _verify_worker_job(
        self,
        session: Session,
        *,
        task: TaskDB,
        dispatch: Mapping[str, str],
        agent_url: str,
        capacity_limit: int,
        active_task_count: int,
        referenced_job_ids: set[str],
    ) -> None:
        worker_job_id = str(dispatch.get("worker_job_id") or "")
        job_statement = select(WorkerJobDB).where(
            WorkerJobDB.id == worker_job_id
        )
        if self._supports_row_lock(session):
            job_statement = job_statement.with_for_update()
        job = session.exec(job_statement).one_or_none()
        now = float(self._clock())
        if (
            job is None
            or str(job.parent_task_id or "") != str(task.id or "")
            or str(job.subtask_id or "")
            != str(dispatch.get("subtask_id") or "")
            or str(job.worker_url or "") != agent_url
            or str(dispatch.get("worker_url") or "") != agent_url
            or str(job.context_bundle_id or "")
            != str(dispatch.get("context_bundle_id") or "")
            or str(job.status or "") != "delegated"
            or str(job.selected_worker_id or agent_url) != agent_url
            or float(job.created_at or 0) > now + 30
            or now - float(job.created_at or 0) > 600
        ):
            self._fail("category_research_dispatch_lease_invalid")

        active_jobs = list(
            session.exec(
                select(WorkerJobDB)
                .where(
                    WorkerJobDB.worker_url == agent_url,
                    WorkerJobDB.status.in_(_ACTIVE_JOB_STATUSES),
                )
                .order_by(WorkerJobDB.created_at, WorkerJobDB.id)
            ).all()
        )
        pending_jobs = [
            row
            for row in active_jobs
            if str(row.id or "") not in referenced_job_ids
        ]
        available = max(0, int(capacity_limit) - active_task_count)
        admitted_ids = {
            str(row.id) for row in pending_jobs[:available]
        }
        if worker_job_id not in referenced_job_ids | admitted_ids:
            self._fail("category_research_assignment_capacity_exhausted")

        if job.slot_lease_id:
            lease = session.get(WorkerSlotLeaseDB, str(job.slot_lease_id))
            if (
                lease is None
                or str(lease.status or "") != "active"
                or lease.released_at is not None
                or float(lease.deadline_at or 0) <= now
                or str(lease.worker_job_id or "")
                not in {"", worker_job_id}
                or str(lease.parent_task_id or "")
                not in {"", str(task.id or "")}
            ):
                self._fail("category_research_worker_slot_lease_invalid")

    @classmethod
    def _verify_persisted_binding(
        cls,
        task: TaskDB,
        *,
        binding: Mapping[str, Any],
        routing: Mapping[str, Any],
    ) -> None:
        assignment_id = str(binding.get("assignment_id") or "")
        agent_url = str(binding.get("agent_url") or "")
        binding_payload = {
            key: value
            for key, value in dict(binding).items()
            if key != "binding_digest"
        }
        routing_payload = {
            key: value
            for key, value in dict(routing).items()
            if key != "decision_hash"
        }
        if (
            binding.get("schema") != _ASSIGNMENT_BINDING_SCHEMA
            or routing.get("schema") != _ROUTING_SCHEMA
            or not assignment_id
            or not agent_url
            or str(task.assigned_agent_url or "") != agent_url
            or str(binding.get("tenant_id") or "")
            != str(task.tenant_id or "")
            or str(binding.get("project_id") or "")
            != str(task.project_id or "")
            or str(binding.get("organization_id") or "")
            != str(task.organization_id or "")
            or str(binding.get("goal_id") or "")
            != str(task.goal_id or "")
            or str(binding.get("unit_id") or "")
            != str(task.unit_id or "")
            or str(binding.get("team_id") or "")
            != str(task.team_id or "")
            or str(binding.get("role_slot_id") or "")
            != str(task.role_slot_id or "")
            or str(binding.get("binding_digest") or "")
            != cls._digest(binding_payload)
            or str(routing.get("selected_agent_id") or "") != agent_url
            or str(routing.get("selected_assignment_id") or "")
            != assignment_id
            or str(routing.get("selected_team_id") or "")
            != str(task.team_id or "")
            or str(routing.get("selected_role_slot_id") or "")
            != str(task.role_slot_id or "")
            or str(routing.get("assignment_binding_digest") or "")
            != str(binding.get("binding_digest") or "")
            or str(routing.get("decision_hash") or "")
            != cls._digest(
                {
                    "task_id": str(task.id or ""),
                    "organization_routing": routing_payload,
                }
            )
        ):
            cls._fail("category_research_assignment_binding_invalid")

    @staticmethod
    def _supports_row_lock(session: Session) -> bool:
        bind = session.get_bind()
        return bool(bind is not None and bind.dialect.name != "sqlite")

    @staticmethod
    def _mapping(value: Any) -> Mapping[str, Any]:
        return value if isinstance(value, Mapping) else {}

    @staticmethod
    def _digest(value: Any) -> str:
        return hashlib.sha256(
            json.dumps(
                value,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _fail(reason_code: str) -> None:
        raise OrganizationResearchAssignmentBindingError(reason_code)


__all__ = [
    "OrganizationResearchAssignmentBindingError",
    "OrganizationResearchAssignmentBindingService",
]
