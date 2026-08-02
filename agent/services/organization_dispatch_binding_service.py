"""Resolve only Hub-persisted Organization planning dispatch bindings."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from typing import Any

from sqlmodel import Session, select

from agent.db_models import (
    OrganizationRoleAssignmentDB,
    PlanningArtifactRevisionDB,
    PlanningTaskDispatchDB,
    PlanningTaskMappingDB,
    TaskDB,
)


class OrganizationDispatchBindingResolver:
    """Read-only guard between the generic delegation API and Planning outbox.

    A JSON-shaped routing decision is never sufficient authority. The exact
    Task, outbox lease, immutable mapping, adopted Track policy, and active
    role assignment must all still agree in Hub persistence.
    """

    def __init__(
        self,
        *,
        session_factory: Callable[[], Session] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._session_factory = session_factory or self._default_session
        self._clock = clock

    @staticmethod
    def _default_session() -> Session:
        from agent.database import engine

        return Session(engine)

    def resolve(self, task: Mapping[str, Any]) -> str | None:
        task_id = str(task.get("id") or "").strip()
        tenant_id = str(task.get("tenant_id") or "").strip()
        project_id = str(task.get("project_id") or "").strip()
        organization_id = str(task.get("organization_id") or "").strip()
        if not all((task_id, tenant_id, project_id, organization_id)):
            return None
        with self._session_factory() as session:
            authoritative = session.exec(
                select(TaskDB).where(
                    TaskDB.id == task_id,
                    TaskDB.tenant_id == tenant_id,
                    TaskDB.project_id == project_id,
                    TaskDB.organization_id == organization_id,
                )
            ).one_or_none()
            if authoritative is None:
                return None
            worker_context = dict(authoritative.worker_execution_context or {})
            routing = self._mapping(worker_context.get("organization_routing"))
            dispatch_binding = self._mapping(worker_context.get("planning_dispatch"))
            if not self._task_binding_valid(
                authoritative,
                routing=routing,
                dispatch_binding=dispatch_binding,
            ):
                return None
            dispatch = session.exec(
                select(PlanningTaskDispatchDB).where(
                    PlanningTaskDispatchDB.dispatch_intent_id == str(dispatch_binding.get("dispatch_intent_id") or ""),
                    PlanningTaskDispatchDB.tenant_id == tenant_id,
                    PlanningTaskDispatchDB.project_id == project_id,
                    PlanningTaskDispatchDB.organization_id == organization_id,
                    PlanningTaskDispatchDB.internal_task_id == task_id,
                    PlanningTaskDispatchDB.track_revision_id == str(dispatch_binding.get("track_revision_id") or ""),
                    PlanningTaskDispatchDB.lease_id == str(dispatch_binding.get("lease_id") or ""),
                    PlanningTaskDispatchDB.requested_worker_id == str(routing.get("selected_agent_id") or ""),
                    PlanningTaskDispatchDB.status == "dispatching",
                )
            ).one_or_none()
            if (
                dispatch is None
                or not str(dispatch.processing_owner or "")
                or float(dispatch.processing_lease_expires_at or 0) <= self._clock()
                or dispatch.attempt != self._positive_int(dispatch_binding.get("attempt"))
            ):
                return None
            mapping = session.get(PlanningTaskMappingDB, dispatch.task_mapping_id)
            if (
                mapping is None
                or mapping.internal_task_id != authoritative.id
                or mapping.track_revision_id != dispatch.track_revision_id
                or mapping.plan_task_id != str(dispatch_binding.get("plan_task_id") or "")
                or mapping.team_id != str(authoritative.team_id or "")
                or mapping.role_slot_id != str(authoritative.role_slot_id or "")
                or mapping.unit_id != str(authoritative.unit_id or "")
            ):
                return None
            track = session.get(PlanningArtifactRevisionDB, dispatch.track_revision_id)
            if (
                track is None
                or track.status != "adopted"
                or track.tenant_id != tenant_id
                or track.project_id != project_id
                or track.organization_id != organization_id
                or track.policy_hash != str(routing.get("effective_policy_hash") or "")
            ):
                return None
            assignment = session.exec(
                select(OrganizationRoleAssignmentDB).where(
                    OrganizationRoleAssignmentDB.id == str(routing.get("selected_assignment_id") or ""),
                    OrganizationRoleAssignmentDB.tenant_id == tenant_id,
                    OrganizationRoleAssignmentDB.project_id == project_id,
                    OrganizationRoleAssignmentDB.organization_id == organization_id,
                    OrganizationRoleAssignmentDB.role_slot_id == str(authoritative.role_slot_id or ""),
                    OrganizationRoleAssignmentDB.agent_url == str(routing.get("selected_agent_id") or ""),
                    OrganizationRoleAssignmentDB.lifecycle == "active",
                )
            ).one_or_none()
            return str(assignment.agent_url) if assignment is not None else None

    @staticmethod
    def _task_binding_valid(
        task: TaskDB,
        *,
        routing: Mapping[str, Any],
        dispatch_binding: Mapping[str, Any],
    ) -> bool:
        worker_id = str(routing.get("selected_agent_id") or "")
        return bool(
            routing.get("schema") == "organization_routing_decision.v1"
            and dispatch_binding.get("schema") == "organization_planning_dispatch.v1"
            and str(dispatch_binding.get("status") or "") == "pending_dispatch"
            and worker_id
            and str(task.assigned_agent_url or "") == worker_id
            and str(task.status or "") == "assigned"
            and str(task.status_reason_code or "") == "planning_dispatch_intent_created"
            and str(routing.get("selected_team_id") or "") == str(task.team_id or "")
            and str(routing.get("selected_role_slot_id") or "") == str(task.role_slot_id or "")
            and str(routing.get("selected_assignment_id") or "")
            and str(routing.get("decision_hash") or "")
            and str(dispatch_binding.get("dispatch_intent_id") or "")
            and str(dispatch_binding.get("lease_id") or "")
            and str(dispatch_binding.get("track_revision_id") or "")
            and str(dispatch_binding.get("plan_task_id") or "")
        )

    @staticmethod
    def _mapping(value: Any) -> Mapping[str, Any]:
        return value if isinstance(value, Mapping) else {}

    @staticmethod
    def _positive_int(value: Any) -> int:
        if isinstance(value, bool):
            return 0
        try:
            parsed = int(value)
        except (TypeError, ValueError, OverflowError):
            return 0
        return parsed if parsed > 0 else 0


__all__ = ["OrganizationDispatchBindingResolver"]
