"""Read-only readiness and start policy for Organization Category research."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func
from sqlmodel import Session, select

from agent.db_models import (
    AgentInfoDB,
    GoalDB,
    OrganizationInstanceDB,
    OrganizationMembershipDB,
    OrganizationRoleAssignmentDB,
    OrganizationRoleSlotDB,
    OrganizationTeamLinkDB,
    TaskDB,
)
from agent.services.chat_session_security import ChatSessionPrincipal
from agent.services.organization_assignment_eligibility_service import (
    OrganizationAssignmentEligibilityService,
)
from agent.services.organization_membership_service import (
    OrganizationAccessPrincipal,
    OrganizationMembershipService,
)
from agent.services.planning_artifact_transition_service import (
    PlanningOperationContext,
    PlanningTransitionError,
)
from agent.services.source_catalog_authority_service import (
    ResolvedSourceCatalog,
    SourceCatalogAuthorityError,
    SourceCatalogAuthorityService,
)

CATEGORY_RESEARCH_REQUIRED_CAPABILITIES = frozenset(
    {"planning", "research", "source_analysis"}
)
CATEGORY_RESEARCH_GOAL_STATUSES = frozenset(
    {"received", "planning", "planning_queued", "planning_running", "planned"}
)
CATEGORY_RESEARCH_CATALOG_TASK_SOURCES = frozenset({"agent", "api", "system", "ui"})
CATEGORY_RESEARCH_CATALOG_TASK_KINDS = frozenset(
    {"knowledge", "planning_research", "research", "retrieval", "source_catalog"}
)


@dataclass(frozen=True, slots=True)
class CategoryResearchTargetReadiness:
    blockers: tuple[str, ...]
    organization_lifecycle: str | None
    goal_status: str | None
    team_lifecycle: str | None
    role_slot_lifecycle: str | None
    required_capabilities: tuple[str, ...]
    active_assignment_count: int
    eligible_assignment_count: int
    ineligibility_reasons: tuple[str, ...]
    selected_assignment_id: str | None = None
    selected_agent_url: str | None = None
    selected_capacity_used: int | None = None
    selected_capacity_limit: int | None = None

    @property
    def ready(self) -> bool:
        return not self.blockers

    def as_checks(self) -> dict[str, Any]:
        return {
            "organization": {
                "ready": self.organization_lifecycle == "active",
                "lifecycle": self.organization_lifecycle,
            },
            "goal": {
                "ready": self.goal_status in CATEGORY_RESEARCH_GOAL_STATUSES,
                "status": self.goal_status,
            },
            "team": {
                "ready": self.team_lifecycle == "active",
                "lifecycle": self.team_lifecycle,
            },
            "role_slot": {
                "ready": self.role_slot_lifecycle == "active",
                "lifecycle": self.role_slot_lifecycle,
            },
            "assignment": {
                "ready": self.eligible_assignment_count > 0,
                "active_count": self.active_assignment_count,
                "eligible_count": self.eligible_assignment_count,
                "required_capabilities": list(self.required_capabilities),
                "ineligibility_reasons": list(self.ineligibility_reasons),
            },
        }


class OrganizationCategoryResearchReadinessService:
    """Project a browser-safe preflight and enforce the same start policy."""

    def __init__(
        self,
        *,
        session_factory: Callable[[], Session] | None = None,
        source_catalog_authority: SourceCatalogAuthorityService | None = None,
        task_reader: Callable[[str], Any | None] | None = None,
        assignment_eligibility: OrganizationAssignmentEligibilityService | None = None,
        membership_service: OrganizationMembershipService | None = None,
    ) -> None:
        self._session_factory = session_factory or self._default_session
        self._catalog_authority = source_catalog_authority or SourceCatalogAuthorityService()
        self._task_reader = task_reader or self._default_task_reader
        self._assignment_eligibility = assignment_eligibility or OrganizationAssignmentEligibilityService()
        self._membership = membership_service or OrganizationMembershipService(
            session_factory=self._session_factory,
        )

    @staticmethod
    def _default_session() -> Session:
        from agent.database import engine

        return Session(engine)

    @staticmethod
    def _default_task_reader(task_id: str) -> Any | None:
        from agent.repository import task_repo

        return task_repo.get_by_id(task_id)

    def evaluate(
        self,
        *,
        context: PlanningOperationContext,
        goal_id: str,
        unit_id: str,
        team_id: str,
        role_slot_id: str,
        catalog_task_id: str,
    ) -> dict[str, Any]:
        self._authorize(context)
        self._require_current_membership(context)
        with self._session_factory() as session:
            target = self.inspect_target(
                session,
                context=context,
                goal_id=goal_id,
                unit_id=unit_id,
                team_id=team_id,
                role_slot_id=role_slot_id,
                lock_rows=False,
            )

        catalog_binding: dict[str, str] | None = None
        source_count = 0
        blockers = list(target.blockers)
        try:
            resolved = self._resolve_catalog_selector(
                context=context,
                catalog_task_id=catalog_task_id,
            )
            catalog_binding = self._catalog_binding(resolved)
            source_count = len(resolved.source_refs)
        except SourceCatalogAuthorityError:
            blockers.append("category_research_source_catalog_unavailable")

        normalized_blockers = list(dict.fromkeys(blockers))
        checks = target.as_checks()
        checks["source_catalog"] = {
            "ready": catalog_binding is not None,
            "source_count": source_count,
        }
        return {
            "schema": "organization_category_research_readiness.v1",
            "tenant_id": context.tenant_id,
            "project_id": context.project_id,
            "organization_id": context.organization_id,
            "goal_id": goal_id,
            "target": {
                "unit_id": unit_id,
                "team_id": team_id,
                "role_slot_id": role_slot_id,
            },
            "ready": not normalized_blockers,
            "blockers": [{"reason_code": reason} for reason in normalized_blockers],
            "checks": checks,
            "source_catalog_binding": catalog_binding,
            "task_write": False,
            "queue_write": False,
        }

    def require_start_ready(
        self,
        session: Session,
        *,
        context: PlanningOperationContext,
        goal_id: str,
        unit_id: str,
        team_id: str,
        role_slot_id: str,
    ) -> CategoryResearchTargetReadiness:
        """Lock and revalidate routing authority immediately before Task write."""

        self._authorize(context)
        self._require_current_membership(
            context,
            session=session,
            lock_rows=True,
        )
        readiness = self.inspect_target(
            session,
            context=context,
            goal_id=goal_id,
            unit_id=unit_id,
            team_id=team_id,
            role_slot_id=role_slot_id,
            lock_rows=True,
        )
        if readiness.blockers:
            raise PlanningTransitionError(readiness.blockers[0])
        return readiness

    def inspect_target(
        self,
        session: Session,
        *,
        context: PlanningOperationContext,
        goal_id: str,
        unit_id: str,
        team_id: str,
        role_slot_id: str,
        lock_rows: bool,
    ) -> CategoryResearchTargetReadiness:
        organization_statement = select(OrganizationInstanceDB).where(
            OrganizationInstanceDB.organization_id == context.organization_id,
            OrganizationInstanceDB.tenant_id == context.tenant_id,
            OrganizationInstanceDB.project_id == context.project_id,
        )
        goal_statement = select(GoalDB).where(
            GoalDB.id == goal_id,
            GoalDB.tenant_id == context.tenant_id,
            GoalDB.project_id == context.project_id,
            GoalDB.organization_id == context.organization_id,
            GoalDB.goal_kind == "organization",
        )
        team_statement = select(OrganizationTeamLinkDB).where(
            OrganizationTeamLinkDB.tenant_id == context.tenant_id,
            OrganizationTeamLinkDB.project_id == context.project_id,
            OrganizationTeamLinkDB.organization_id == context.organization_id,
            OrganizationTeamLinkDB.unit_id == unit_id,
            OrganizationTeamLinkDB.team_id == team_id,
        )
        slot_statement = select(OrganizationRoleSlotDB).where(
            OrganizationRoleSlotDB.id == role_slot_id,
            OrganizationRoleSlotDB.tenant_id == context.tenant_id,
            OrganizationRoleSlotDB.project_id == context.project_id,
            OrganizationRoleSlotDB.organization_id == context.organization_id,
            OrganizationRoleSlotDB.unit_id == unit_id,
        )
        if lock_rows and self._supports_row_lock(session):
            organization_statement = organization_statement.with_for_update()
            goal_statement = goal_statement.with_for_update()
            team_statement = team_statement.with_for_update()
            slot_statement = slot_statement.with_for_update()

        organization = session.exec(organization_statement).one_or_none()
        goal = session.exec(goal_statement).one_or_none()
        team = session.exec(team_statement).one_or_none()
        slot = session.exec(slot_statement).one_or_none()

        blockers: list[str] = []
        organization_lifecycle = str(organization.lifecycle or "") if organization is not None else None
        if organization is None:
            blockers.append("organization_planning_not_found")
        elif organization_lifecycle != "active":
            blockers.append("category_research_organization_not_active")
        if goal is None:
            blockers.append("organization_goal_not_found")
        elif str(goal.status or "").strip().lower() not in CATEGORY_RESEARCH_GOAL_STATUSES:
            blockers.append("category_research_goal_not_ready")
        team_lifecycle = str(team.lifecycle or "") if team is not None else None
        if team is None or team_lifecycle != "active":
            blockers.append("category_research_team_not_active")
        # A Role Slot belongs to its Team through the Unit.  Team links are
        # unique per Organization Unit, so the exact Team query above and the
        # Slot's exact unit_id jointly form the authoritative Team binding.
        slot_lifecycle = str(slot.lifecycle or "") if slot is not None else None
        if slot is None or slot_lifecycle != "active":
            blockers.append("category_research_role_slot_not_active")

        required_capabilities = set(CATEGORY_RESEARCH_REQUIRED_CAPABILITIES)
        active_assignments: list[OrganizationRoleAssignmentDB] = []
        eligible_assignment_count = 0
        eligible_assignments: list[tuple[OrganizationRoleAssignmentDB, Any]] = []
        ineligibility_reasons: set[str] = set()
        if slot is not None and slot_lifecycle == "active":
            slot_policy = dict(slot.assignment_policy or {})
            required_capabilities.update(
                str(value).strip()
                for value in list(slot_policy.get("required_capabilities") or [])
                if str(value).strip()
            )
            assignment_statement = select(OrganizationRoleAssignmentDB).where(
                OrganizationRoleAssignmentDB.tenant_id == context.tenant_id,
                OrganizationRoleAssignmentDB.project_id == context.project_id,
                OrganizationRoleAssignmentDB.organization_id == context.organization_id,
                OrganizationRoleAssignmentDB.role_slot_id == role_slot_id,
                OrganizationRoleAssignmentDB.lifecycle == "active",
            )
            if lock_rows and self._supports_row_lock(session):
                assignment_statement = assignment_statement.with_for_update()
            active_assignments = list(session.exec(assignment_statement).all())
            agent_urls = sorted({row.agent_url for row in active_assignments if row.agent_url})
            agent_statement = select(AgentInfoDB).where(AgentInfoDB.url.in_(agent_urls))
            if lock_rows and self._supports_row_lock(session):
                agent_statement = agent_statement.with_for_update()
            agents = {
                row.url: row
                for row in (list(session.exec(agent_statement).all()) if agent_urls else [])
            }
            capacity_by_agent: dict[str, int] = {}
            for assignment in active_assignments:
                if assignment.agent_url not in capacity_by_agent:
                    capacity_by_agent[assignment.agent_url] = int(
                        session.exec(
                            select(func.count())
                            .select_from(TaskDB)
                            .where(
                                TaskDB.assigned_agent_url == assignment.agent_url,
                                TaskDB.status.in_(("assigned", "in_progress")),
                            )
                        ).one()
                        or 0
                    )
                eligibility = self._assignment_eligibility.evaluate(
                    agent=agents.get(assignment.agent_url),
                    required_capabilities=required_capabilities,
                    forbidden_capabilities={
                        str(value).strip()
                        for value in list(slot_policy.get("forbidden_capabilities") or [])
                        if str(value).strip()
                    },
                    capacity_used=capacity_by_agent[assignment.agent_url],
                    principal_kind_allowed="agent"
                    in {
                        str(value).strip()
                        for value in list(slot_policy.get("principal_kinds") or [])
                    },
                    write_access_required=bool(slot_policy.get("write_access_required", False)),
                )
                if eligibility.allowed:
                    eligible_assignment_count += 1
                    eligible_assignments.append((assignment, eligibility))
                else:
                    ineligibility_reasons.update(eligibility.reasons)

            if not active_assignments:
                blockers.append("category_research_active_assignment_required")
            elif eligible_assignment_count == 0:
                blockers.append("category_research_eligible_assignment_required")

        selected = min(
            eligible_assignments,
            key=lambda item: (
                item[1].capacity_used / max(1, item[1].capacity_limit),
                str(item[0].agent_url or ""),
                str(item[0].id or ""),
            ),
            default=None,
        )
        return CategoryResearchTargetReadiness(
            blockers=tuple(blockers),
            organization_lifecycle=organization_lifecycle,
            goal_status=str(goal.status or "").strip().lower() if goal is not None else None,
            team_lifecycle=team_lifecycle,
            role_slot_lifecycle=slot_lifecycle,
            required_capabilities=tuple(sorted(required_capabilities)),
            active_assignment_count=len(active_assignments),
            eligible_assignment_count=eligible_assignment_count,
            ineligibility_reasons=tuple(sorted(ineligibility_reasons)),
            selected_assignment_id=(
                str(selected[0].id) if selected is not None else None
            ),
            selected_agent_url=(
                str(selected[0].agent_url) if selected is not None else None
            ),
            selected_capacity_used=(
                int(selected[1].capacity_used)
                if selected is not None
                else None
            ),
            selected_capacity_limit=(
                int(selected[1].capacity_limit)
                if selected is not None
                else None
            ),
        )

    def _resolve_catalog_selector(
        self,
        *,
        context: PlanningOperationContext,
        catalog_task_id: str,
    ) -> ResolvedSourceCatalog:
        task_id = str(catalog_task_id or "").strip()
        if not task_id:
            raise SourceCatalogAuthorityError("source_catalog_task_id_required")
        task = self._task_reader(task_id)
        if task is None:
            raise SourceCatalogAuthorityError("source_catalog_task_not_found")
        raw_task = task.model_dump() if hasattr(task, "model_dump") else task
        if not isinstance(raw_task, Mapping):
            raise SourceCatalogAuthorityError("source_catalog_task_invalid")
        verification = raw_task.get("verification_status")
        if not isinstance(verification, Mapping):
            raise SourceCatalogAuthorityError("source_catalog_schema_invalid")
        raw_catalog = verification.get("source_catalog")
        if not isinstance(raw_catalog, Mapping):
            raise SourceCatalogAuthorityError("source_catalog_schema_invalid")
        catalog = dict(raw_catalog)
        raw_sources = catalog.get("sources")
        if not isinstance(raw_sources, list) or not raw_sources:
            raise SourceCatalogAuthorityError("source_catalog_sources_invalid")
        references: list[dict[str, Any]] = []
        for source in raw_sources:
            if not isinstance(source, Mapping) or not isinstance(source.get("source_ref"), Mapping):
                raise SourceCatalogAuthorityError("source_catalog_source_invalid")
            references.append(dict(source["source_ref"]))
        revisions = {str(row.get("source_version") or "").strip() for row in references}
        scopes = {str(row.get("scope") or "").strip() for row in references}
        if len(revisions) != 1 or not next(iter(revisions), ""):
            raise SourceCatalogAuthorityError("source_catalog_repository_revision_ambiguous")
        if len(scopes) != 1 or not next(iter(scopes), ""):
            raise SourceCatalogAuthorityError("source_catalog_scope_ambiguous")
        expected_scope = f"organization:{context.organization_id}"
        if next(iter(scopes)) != expected_scope:
            raise SourceCatalogAuthorityError("source_catalog_scope_forbidden")
        catalog_hash = str(catalog.get("source_catalog_hash") or "").strip()
        return self._catalog_authority.resolve(
            principal=ChatSessionPrincipal.from_values(context.tenant_id, context.subject_id),
            catalog_task_id=task_id,
            catalog_id=str(catalog.get("source_catalog_id") or "").strip(),
            catalog_hash=catalog_hash,
            repository_revision=next(iter(revisions)),
            manifest_hash=str(catalog.get("retrieval_manifest_hash") or "").strip(),
            source_allowlist_version=catalog_hash,
            source_scope=next(iter(scopes)),
            allowed_task_sources=CATEGORY_RESEARCH_CATALOG_TASK_SOURCES,
            allowed_task_kinds=CATEGORY_RESEARCH_CATALOG_TASK_KINDS,
            expected_task_tenant_id=context.tenant_id,
            expected_task_project_id=context.project_id,
            expected_task_organization_id=context.organization_id,
            organization_access_authorized=True,
        )

    @staticmethod
    def _catalog_binding(resolved: ResolvedSourceCatalog) -> dict[str, str]:
        if not resolved.source_refs:
            raise SourceCatalogAuthorityError("source_catalog_sources_invalid")
        return {
            "catalog_task_id": resolved.catalog_task_id,
            "catalog_id": resolved.catalog_id,
            "catalog_hash": resolved.catalog_hash,
            "repository_revision": resolved.repository_revision,
            "manifest_hash": resolved.manifest_hash,
            "source_allowlist_version": resolved.source_allowlist_version,
            "source_scope": resolved.source_refs[0].scope,
        }

    @staticmethod
    def _authorize(context: PlanningOperationContext) -> None:
        if not context.hub_owned:
            raise PlanningTransitionError("planning_hub_authority_required")
        if "organization_admin" not in context.roles and "category_research" not in context.allowed_operations:
            raise PlanningTransitionError("planning_organization_admin_required")

    def _require_current_membership(
        self,
        context: PlanningOperationContext,
        *,
        session: Session | None = None,
        lock_rows: bool = False,
    ) -> None:
        principal = OrganizationAccessPrincipal(
            principal_id=context.subject_id,
            tenant_id=context.tenant_id,
            project_id=context.project_id,
        )
        if session is None:
            authorized = self._membership.can_view(
                principal=principal,
                tenant_id=context.tenant_id,
                project_id=context.project_id,
                organization_id=context.organization_id,
            )
        else:
            statement = select(OrganizationMembershipDB).where(
                OrganizationMembershipDB.tenant_id == context.tenant_id,
                OrganizationMembershipDB.project_id == context.project_id,
                OrganizationMembershipDB.organization_id
                == context.organization_id,
                OrganizationMembershipDB.principal_id == context.subject_id,
            )
            if lock_rows and self._supports_row_lock(session):
                statement = statement.with_for_update()
            membership = session.exec(statement).one_or_none()
            authorized = bool(
                membership is not None
                and (
                    membership.expires_at is None
                    or float(membership.expires_at) >= time.time()
                )
            )
        if not authorized:
            raise PlanningTransitionError("organization_planning_not_found")

    @staticmethod
    def _supports_row_lock(session: Session) -> bool:
        return str(getattr(getattr(session.get_bind(), "dialect", None), "name", "")) == "postgresql"


__all__ = [
    "CATEGORY_RESEARCH_CATALOG_TASK_KINDS",
    "CATEGORY_RESEARCH_CATALOG_TASK_SOURCES",
    "CATEGORY_RESEARCH_GOAL_STATUSES",
    "CATEGORY_RESEARCH_REQUIRED_CAPABILITIES",
    "CategoryResearchTargetReadiness",
    "OrganizationCategoryResearchReadinessService",
]
