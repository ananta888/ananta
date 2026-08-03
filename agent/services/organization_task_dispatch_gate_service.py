"""Hub-owned lifecycle fence for Organization Task dispatch."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from sqlmodel import Session, select

from agent.db_models import OrganizationInstanceDB


def organization_research_requires_secure_delegation(
    task: Mapping[str, Any] | Any,
) -> bool:
    """Identify Hub-owned research that may use only the secure intake path."""

    from agent.services.organization_planning_adapter import (
        organization_id_from_task,
    )

    task_kind = (
        task.get("task_kind")
        if isinstance(task, Mapping)
        else getattr(task, "task_kind", None)
    )
    return bool(
        str(task_kind or "").strip().lower() == "planning_research"
        and organization_id_from_task(task)
    )


@dataclass(frozen=True, slots=True)
class OrganizationTaskDispatchDecision:
    allowed: bool
    reason_code: str


class OrganizationTaskDispatchGateService:
    """Authorize dispatch from authoritative Organization lifecycle state."""

    def __init__(
        self,
        *,
        session_factory: Callable[[], Session] | None = None,
    ) -> None:
        self._session_factory = session_factory or self._default_session

    @staticmethod
    def _default_session() -> Session:
        from agent.database import engine

        return Session(engine)

    def evaluate(self, task: Mapping[str, Any] | Any) -> OrganizationTaskDispatchDecision:
        organization_id = self._value(task, "organization_id")
        if organization_research_requires_secure_delegation(task):
            return OrganizationTaskDispatchDecision(
                allowed=False,
                reason_code=(
                    "organization_research_secure_delegation_required"
                ),
            )
        if not organization_id:
            return OrganizationTaskDispatchDecision(
                allowed=True,
                reason_code="task_not_organization_scoped",
            )
        tenant_id = self._value(task, "tenant_id")
        project_id = self._value(task, "project_id")
        if not tenant_id or not project_id:
            return OrganizationTaskDispatchDecision(
                allowed=False,
                reason_code="organization_dispatch_scope_missing",
            )
        with self._session_factory() as session:
            organization = session.exec(
                select(OrganizationInstanceDB).where(
                    OrganizationInstanceDB.tenant_id == tenant_id,
                    OrganizationInstanceDB.project_id == project_id,
                    OrganizationInstanceDB.organization_id == organization_id,
                )
            ).one_or_none()
        if organization is None:
            return OrganizationTaskDispatchDecision(
                allowed=False,
                reason_code="organization_dispatch_scope_not_found",
            )
        lifecycle = str(organization.lifecycle or "").strip().lower()
        if lifecycle != "active":
            return OrganizationTaskDispatchDecision(
                allowed=False,
                reason_code=f"organization_dispatch_lifecycle_{lifecycle or 'invalid'}",
            )
        return OrganizationTaskDispatchDecision(
            allowed=True,
            reason_code="organization_dispatch_allowed",
        )

    @staticmethod
    def _value(task: Mapping[str, Any] | Any, key: str) -> str:
        value = task.get(key) if isinstance(task, Mapping) else getattr(task, key, None)
        return str(value or "").strip()


_SERVICE = OrganizationTaskDispatchGateService()


def get_organization_task_dispatch_gate_service() -> OrganizationTaskDispatchGateService:
    return _SERVICE


__all__ = [
    "OrganizationTaskDispatchDecision",
    "OrganizationTaskDispatchGateService",
    "get_organization_task_dispatch_gate_service",
    "organization_research_requires_secure_delegation",
]
