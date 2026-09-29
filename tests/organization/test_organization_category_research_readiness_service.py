from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from agent.db_models import (
    AgentInfoDB,
    ContextBundleDB,
    GoalDB,
    OrganizationInstanceDB,
    OrganizationMembershipDB,
    OrganizationRoleAssignmentDB,
    OrganizationRoleSlotDB,
    OrganizationTeamLinkDB,
    RetrievalRunDB,
    TaskDB,
    WorkerJobDB,
)
from agent.services.organization_category_research_readiness_service import (
    OrganizationCategoryResearchReadinessService,
)
from agent.services.organization_category_research_service import (
    OrganizationCategoryResearchService,
)
from agent.services.organization_research_assignment_binding_service import (
    OrganizationResearchAssignmentBindingError,
    OrganizationResearchAssignmentBindingService,
)
from agent.services.organization_source_catalog_context_service import (
    MaterializedOrganizationSourceCatalogContext,
)
from agent.services.planning_artifact_transition_service import (
    PlanningOperationContext,
    PlanningTransitionError,
)
from agent.services.planning_control_unit_of_work import PlanningControlUnitOfWork
from agent.services.source_catalog_authority_service import ResolvedSourceCatalog, SourceCatalogAuthorityError

_CATALOG_HASH = "a" * 64
_MANIFEST_HASH = "b" * 64
_REVISION = "c" * 64


class _CatalogAuthority:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def resolve(self, **kwargs: Any) -> ResolvedSourceCatalog:
        self.calls.append(kwargs)
        return ResolvedSourceCatalog(
            catalog_task_id=kwargs["catalog_task_id"],
            catalog_id=kwargs["catalog_id"],
            catalog_hash=kwargs["catalog_hash"],
            repository_revision=kwargs["repository_revision"],
            manifest_hash=kwargs["manifest_hash"],
            source_allowlist_version=kwargs["source_allowlist_version"],
            source_refs=(
                SimpleNamespace(source_id="TEST_SOURCE_REF", scope=kwargs["source_scope"]),
            ),
        )


class _MaterializingCatalogContext:
    def __init__(self, authority: _CatalogAuthority) -> None:
        self.authority = authority

    def materialize(
        self,
        session: Session,
        *,
        context: PlanningOperationContext,
        catalog_binding: dict[str, Any],
        task_id: str,
        goal_id: str,
    ) -> MaterializedOrganizationSourceCatalogContext:
        resolved = self.authority.resolve(
            principal=SimpleNamespace(),
            catalog_task_id=catalog_binding["catalog_task_id"],
            catalog_id=catalog_binding["catalog_id"],
            catalog_hash=catalog_binding["catalog_hash"],
            repository_revision=catalog_binding["repository_revision"],
            manifest_hash=catalog_binding["manifest_hash"],
            source_allowlist_version=catalog_binding["source_allowlist_version"],
            source_scope=catalog_binding["source_scope"],
            allowed_task_sources=frozenset({"api"}),
            allowed_task_kinds=frozenset({"source_catalog"}),
            expected_task_tenant_id=context.tenant_id,
            expected_task_project_id=context.project_id,
            expected_task_organization_id=context.organization_id,
            organization_access_authorized=True,
        )
        retrieval = RetrievalRunDB(
            id=f"retrieval-{task_id}",
            query="source-catalog:catalog-1",
            task_id=task_id,
            goal_id=goal_id,
        )
        bundle = ContextBundleDB(
            id=f"context-{task_id}",
            retrieval_run_id=retrieval.id,
            task_id=task_id,
            chunks=[
                {
                    "engine": "organization_source_catalog",
                    "source": "TEST_SOURCE_REF",
                    "content": "test-only context",
                    "metadata": {"source_id": "TEST_SOURCE_REF"},
                }
            ],
        )
        session.add(retrieval)
        session.add(bundle)
        return MaterializedOrganizationSourceCatalogContext(
            resolved_catalog=resolved,
            source_catalog={
                "schema": "source_catalog.v2",
                "source_catalog_id": resolved.catalog_id,
                "source_catalog_hash": resolved.catalog_hash,
                "sources": _catalog_task()["verification_status"]["source_catalog"][
                    "sources"
                ],
            },
            retrieval_run=retrieval,
            context_bundle=bundle,
        )


def _catalog_task() -> dict[str, Any]:
    return {
        "id": "catalog-task-1",
        "verification_status": {
            "source_catalog": {
                "schema": "source_catalog.v2",
                "source_catalog_id": "catalog-1",
                "source_catalog_hash": _CATALOG_HASH,
                "retrieval_manifest_hash": _MANIFEST_HASH,
                "sources": [
                    {
                        "source_id": "TEST_SOURCE_REF",
                        "source_ref": {
                            "source_version": _REVISION,
                            "scope": "organization:org-1",
                        },
                    }
                ],
            }
        },
    }


def _context() -> PlanningOperationContext:
    return PlanningOperationContext.hub_admin(
        subject_id="operator-1",
        tenant_id="tenant-1",
        project_id="project-1",
        organization_id="org-1",
    )


@pytest.fixture()
def engine():
    database = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(
        database,
        tables=[
            AgentInfoDB.__table__,
            OrganizationInstanceDB.__table__,
            OrganizationMembershipDB.__table__,
            OrganizationTeamLinkDB.__table__,
            OrganizationRoleSlotDB.__table__,
            OrganizationRoleAssignmentDB.__table__,
            GoalDB.__table__,
            TaskDB.__table__,
            RetrievalRunDB.__table__,
            ContextBundleDB.__table__,
            WorkerJobDB.__table__,
        ],
    )
    with Session(database) as session:
        session.add(
            OrganizationInstanceDB(
                organization_id="org-1",
                tenant_id="tenant-1",
                project_id="project-1",
                name="Research organization",
                definition_key="test-definition",
                definition_version=1,
                definition_revision="d" * 64,
                lifecycle="active",
                effective_limit_profile_ref="test-limit@1",
                effective_limit_profile_revision=1,
                effective_limit_profile_hash="e" * 64,
                composition_mode="standard",
                plan_digest="f" * 64,
                idempotency_key="organization-key-1",
            )
        )
        session.add(
            OrganizationMembershipDB(
                tenant_id="tenant-1",
                project_id="project-1",
                organization_id="org-1",
                principal_id="operator-1",
                membership_kind="organization_admin",
            )
        )
        session.add(
            GoalDB(
                id="goal-1",
                goal="Research HRM readiness",
                status="planned",
                goal_kind="organization",
                organization_id="org-1",
                tenant_id="tenant-1",
                project_id="project-1",
            )
        )
        session.add(
            OrganizationTeamLinkDB(
                id="team-link-1",
                tenant_id="tenant-1",
                project_id="project-1",
                organization_id="org-1",
                unit_id="unit-1",
                team_id="team-1",
                lifecycle="active",
            )
        )
        session.add(
            OrganizationRoleSlotDB(
                id="slot-1",
                tenant_id="tenant-1",
                project_id="project-1",
                organization_id="org-1",
                unit_id="unit-1",
                slot_key="research_lead",
                role_template_key="research_lead",
                role_template_version=1,
                assignment_policy={
                    "principal_kinds": ["agent"],
                    "required_capabilities": ["analysis"],
                    "forbidden_capabilities": [],
                    "write_access_required": False,
                },
                lifecycle="active",
            )
        )
        session.add(
            AgentInfoDB(
                url="http://worker.test",
                name="Research worker",
                registration_validated=True,
                status="online",
                authorized_capabilities=[
                    "analysis",
                    "planning",
                    "research",
                    "source_analysis",
                ],
                execution_limits={"max_concurrent_tasks": 2},
            )
        )
        session.add(
            OrganizationRoleAssignmentDB(
                id="assignment-1",
                tenant_id="tenant-1",
                project_id="project-1",
                organization_id="org-1",
                role_slot_id="slot-1",
                agent_url="http://worker.test",
                lifecycle="active",
            )
        )
        session.commit()
    return database


def test_readiness_resolves_server_catalog_selector_without_exposing_agent_url(engine) -> None:
    authority = _CatalogAuthority()
    service = OrganizationCategoryResearchReadinessService(
        session_factory=lambda: Session(engine),
        source_catalog_authority=authority,  # type: ignore[arg-type]
        task_reader=lambda _task_id: _catalog_task(),
    )

    result = service.evaluate(
        context=_context(),
        goal_id="goal-1",
        unit_id="unit-1",
        team_id="team-1",
        role_slot_id="slot-1",
        catalog_task_id="catalog-task-1",
    )

    assert result["ready"] is True
    assert result["blockers"] == []
    assert result["checks"]["assignment"] == {
        "ready": True,
        "active_count": 1,
        "eligible_count": 1,
        "required_capabilities": [
            "analysis",
            "planning",
            "research",
            "source_analysis",
        ],
        "ineligibility_reasons": [],
    }
    assert result["source_catalog_binding"] == {
        "catalog_task_id": "catalog-task-1",
        "catalog_id": "catalog-1",
        "catalog_hash": _CATALOG_HASH,
        "repository_revision": _REVISION,
        "manifest_hash": _MANIFEST_HASH,
        "source_allowlist_version": _CATALOG_HASH,
        "source_scope": "organization:org-1",
    }
    assert result["task_write"] is False
    assert result["queue_write"] is False
    assert "worker.test" not in str(result)
    assert authority.calls[0]["repository_revision"] == _REVISION
    assert authority.calls[0]["source_scope"] == "organization:org-1"
    assert authority.calls[0]["expected_task_tenant_id"] == "tenant-1"
    assert authority.calls[0]["expected_task_project_id"] == "project-1"
    assert authority.calls[0]["expected_task_organization_id"] == "org-1"
    assert authority.calls[0]["organization_access_authorized"] is True


def test_readiness_rejects_expired_current_organization_membership(engine) -> None:
    with Session(engine) as session:
        membership = session.exec(select(OrganizationMembershipDB)).one()
        membership.expires_at = 1.0
        session.add(membership)
        session.commit()
    service = OrganizationCategoryResearchReadinessService(
        session_factory=lambda: Session(engine),
        source_catalog_authority=_CatalogAuthority(),  # type: ignore[arg-type]
        task_reader=lambda _task_id: _catalog_task(),
    )

    with pytest.raises(
        PlanningTransitionError,
        match="organization_planning_not_found",
    ):
        service.evaluate(
            context=_context(),
            goal_id="goal-1",
            unit_id="unit-1",
            team_id="team-1",
            role_slot_id="slot-1",
            catalog_task_id="catalog-task-1",
        )


def test_readiness_reports_lifecycle_and_capability_blockers(engine) -> None:
    with Session(engine) as session:
        organization = session.get(OrganizationInstanceDB, "org-1")
        agent = session.get(AgentInfoDB, "http://worker.test")
        assert organization is not None and agent is not None
        organization.lifecycle = "paused"
        agent.authorized_capabilities = ["analysis", "research"]
        session.add(organization)
        session.add(agent)
        session.commit()
    service = OrganizationCategoryResearchReadinessService(
        session_factory=lambda: Session(engine),
        source_catalog_authority=_CatalogAuthority(),  # type: ignore[arg-type]
        task_reader=lambda _task_id: _catalog_task(),
    )

    result = service.evaluate(
        context=_context(),
        goal_id="goal-1",
        unit_id="unit-1",
        team_id="team-1",
        role_slot_id="slot-1",
        catalog_task_id="catalog-task-1",
    )

    assert result["ready"] is False
    assert [row["reason_code"] for row in result["blockers"]] == [
        "category_research_organization_not_active",
        "category_research_eligible_assignment_required",
    ]
    assert "missing_capability:planning" in result["checks"]["assignment"]["ineligibility_reasons"]


def test_readiness_rejects_terminal_goal_cross_team_slot_and_foreign_source_scope(engine) -> None:
    with Session(engine) as session:
        goal = session.get(GoalDB, "goal-1")
        assert goal is not None
        goal.status = "failed"
        session.add(goal)
        session.commit()
    authority = _CatalogAuthority()
    service = OrganizationCategoryResearchReadinessService(
        session_factory=lambda: Session(engine),
        source_catalog_authority=authority,  # type: ignore[arg-type]
        task_reader=lambda _task_id: _catalog_task(),
    )

    terminal = service.evaluate(
        context=_context(),
        goal_id="goal-1",
        unit_id="unit-1",
        team_id="team-1",
        role_slot_id="slot-1",
        catalog_task_id="catalog-task-1",
    )
    assert {row["reason_code"] for row in terminal["blockers"]} == {
        "category_research_goal_not_ready"
    }
    assert terminal["checks"]["goal"] == {"ready": False, "status": "failed"}

    cross_team = service.evaluate(
        context=_context(),
        goal_id="goal-1",
        unit_id="unit-1",
        team_id="team-2",
        role_slot_id="slot-1",
        catalog_task_id="catalog-task-1",
    )
    assert "category_research_team_not_active" in {
        row["reason_code"] for row in cross_team["blockers"]
    }

    foreign_catalog = _catalog_task()
    foreign_catalog["verification_status"]["source_catalog"]["sources"][0]["source_ref"][
        "scope"
    ] = "repository"
    foreign_authority = _CatalogAuthority()
    foreign_service = OrganizationCategoryResearchReadinessService(
        session_factory=lambda: Session(engine),
        source_catalog_authority=foreign_authority,  # type: ignore[arg-type]
        task_reader=lambda _task_id: foreign_catalog,
    )
    wrong_scope = foreign_service.evaluate(
        context=_context(),
        goal_id="goal-1",
        unit_id="unit-1",
        team_id="team-1",
        role_slot_id="slot-1",
        catalog_task_id="catalog-task-1",
    )
    assert "category_research_source_catalog_unavailable" in {
        row["reason_code"] for row in wrong_scope["blockers"]
    }
    assert foreign_authority.calls == []


def test_start_persists_authoritative_context_and_destination_policy(engine) -> None:
    authority = _CatalogAuthority()
    service = OrganizationCategoryResearchService(
        source_catalog_authority=authority,  # type: ignore[arg-type]
        source_catalog_context=_MaterializingCatalogContext(authority),  # type: ignore[arg-type]
        task_reader=lambda _task_id: _catalog_task(),
        uow_factory=lambda: PlanningControlUnitOfWork(
            session_factory=lambda: Session(engine)
        ),
    )
    binding = {
        "catalog_task_id": "catalog-task-1",
        "catalog_id": "catalog-1",
        "catalog_hash": _CATALOG_HASH,
        "repository_revision": _REVISION,
        "manifest_hash": _MANIFEST_HASH,
        "source_allowlist_version": _CATALOG_HASH,
        "source_scope": "organization:org-1",
    }

    created = service.create_task(
        context=_context(),
        goal_id="goal-1",
        unit_id="unit-1",
        team_id="team-1",
        role_slot_id="slot-1",
        catalog_binding=binding,
        idempotency_key="research-policy-key-1",
    )

    with Session(engine) as session:
        task = session.get(TaskDB, created["task_id"])
        assert task is not None
        assert task.required_capabilities == [
            "analysis",
            "planning",
            "research",
            "source_analysis",
        ]
        assert task.assigned_agent_url == "http://worker.test"
        worker_context = dict(task.worker_execution_context or {})
        assignment_binding = dict(
            worker_context["planning_research_assignment"]
        )
        organization_routing = dict(
            worker_context["organization_routing"]
        )
        research_binding = dict(
            worker_context["planning_research_binding"]
        )
        source_policy = dict(worker_context["source_context_policy"])
        assert task.context_bundle_id == worker_context["context_bundle_id"]
        assert worker_context["llm_scope"] == "local_only"
        assert research_binding["catalog_task_id"] == "catalog-task-1"
        assert research_binding["allowed_run_refs"] == ["RUN_0001"]
        assert research_binding["llm_scope"] == "local_only"
        assert research_binding["context_bundle_id"] == task.context_bundle_id
        assert research_binding["context_bundle_digest"] == source_policy[
            "context_bundle_digest"
        ]
        assert source_policy["mode"] == (
            "authoritative_source_catalog_bundle"
        )
        assert source_policy["destination_policy"] == research_binding[
            "destination_policy"
        ]
        assert assignment_binding["assignment_id"] == "assignment-1"
        assert assignment_binding["agent_url"] == "http://worker.test"
        assert len(assignment_binding["binding_digest"]) == 64
        assert organization_routing["selected_assignment_id"] == (
            "assignment-1"
        )
        assert organization_routing["selected_agent_id"] == (
            "http://worker.test"
        )
        assert organization_routing["assignment_binding_digest"] == (
            assignment_binding["binding_digest"]
        )
        agent = session.get(AgentInfoDB, "http://worker.test")
        assert agent is not None
        agent.execution_limits = {"max_concurrent_tasks": 1}
        task.status = "assigned"
        session.add(agent)
        session.add(task)
        session.flush()
        task_payload = task.model_dump()
        session.add(
            WorkerJobDB(
                id="research-worker-job-1",
                parent_task_id=task.id,
                subtask_id="research-subtask-1",
                worker_url="http://worker.test",
                context_bundle_id=task.context_bundle_id,
                status="delegated",
                selected_worker_id="http://worker.test",
            )
        )
        session.commit()

    assignment_guard = OrganizationResearchAssignmentBindingService(
        session_factory=lambda: Session(engine)
    )
    assert assignment_guard.resolve_worker(task_payload) == (
        "http://worker.test"
    )
    assignment_guard.verify_dispatch(
        task=task_payload,
        worker_url="http://worker.test",
        worker_job_id="research-worker-job-1",
        subtask_id="research-subtask-1",
        context_bundle_id=str(task_payload["context_bundle_id"]),
    )

    with Session(engine) as session:
        job = session.get(WorkerJobDB, "research-worker-job-1")
        assert job is not None
        job.status = "failed"
        session.add(job)
        session.commit()
    with pytest.raises(
        OrganizationResearchAssignmentBindingError,
        match="category_research_dispatch_lease_invalid",
    ):
        assignment_guard.verify_dispatch(
            task=task_payload,
            worker_url="http://worker.test",
            worker_job_id="research-worker-job-1",
            subtask_id="research-subtask-1",
            context_bundle_id=str(task_payload["context_bundle_id"]),
        )

    with Session(engine) as session:
        organization = session.get(OrganizationInstanceDB, "org-1")
        assert organization is not None
        organization.lifecycle = "paused"
        session.add(organization)
        session.commit()
    assert assignment_guard.resolve_worker(task_payload) is None


def test_start_readiness_selects_assignment_deterministically_under_lock(
    engine,
) -> None:
    with Session(engine) as session:
        session.add(
            AgentInfoDB(
                url="http://worker-a.test",
                name="Research worker A",
                registration_validated=True,
                status="online",
                authorized_capabilities=[
                    "analysis",
                    "planning",
                    "research",
                    "source_analysis",
                ],
                execution_limits={"max_concurrent_tasks": 2},
            )
        )
        session.add(
            OrganizationRoleAssignmentDB(
                id="assignment-2",
                tenant_id="tenant-1",
                project_id="project-1",
                organization_id="org-1",
                role_slot_id="slot-1",
                agent_url="http://worker-a.test",
                lifecycle="active",
            )
        )
        session.commit()

    readiness_service = OrganizationCategoryResearchReadinessService()
    with Session(engine) as session:
        readiness = readiness_service.require_start_ready(
            session,
            context=_context(),
            goal_id="goal-1",
            unit_id="unit-1",
            team_id="team-1",
            role_slot_id="slot-1",
        )

    assert readiness.selected_assignment_id == "assignment-2"
    assert readiness.selected_agent_url == "http://worker-a.test"


def test_start_revalidates_active_eligible_assignment_before_task_write(engine) -> None:
    with Session(engine) as session:
        assignment = session.get(OrganizationRoleAssignmentDB, "assignment-1")
        assert assignment is not None
        assignment.lifecycle = "suspended"
        session.add(assignment)
        session.commit()

    authority = _CatalogAuthority()
    service = OrganizationCategoryResearchService(
        source_catalog_authority=authority,  # type: ignore[arg-type]
        source_catalog_context=_MaterializingCatalogContext(authority),  # type: ignore[arg-type]
        task_reader=lambda _task_id: _catalog_task(),
        uow_factory=lambda: PlanningControlUnitOfWork(
            session_factory=lambda: Session(engine)
        ),
    )
    binding = {
        "catalog_task_id": "catalog-task-1",
        "catalog_id": "catalog-1",
        "catalog_hash": _CATALOG_HASH,
        "repository_revision": _REVISION,
        "manifest_hash": _MANIFEST_HASH,
        "source_allowlist_version": _CATALOG_HASH,
        "source_scope": "organization:org-1",
    }

    with pytest.raises(PlanningTransitionError, match="category_research_active_assignment_required"):
        service.create_task(
            context=_context(),
            goal_id="goal-1",
            unit_id="unit-1",
            team_id="team-1",
            role_slot_id="slot-1",
            catalog_binding=binding,
            idempotency_key="research-start-key-1",
        )

    with Session(engine) as session:
        assert list(session.exec(select(TaskDB)).all()) == []
        assert list(session.exec(select(RetrievalRunDB)).all()) == []
        assert list(session.exec(select(ContextBundleDB)).all()) == []
    assert len(authority.calls) == 1


def test_start_revalidates_catalog_authority_before_task_write(engine) -> None:
    class RevokedCatalogAuthority(_CatalogAuthority):
        def resolve(self, **kwargs: Any) -> ResolvedSourceCatalog:
            if self.calls:
                raise SourceCatalogAuthorityError("source_catalog_not_current")
            return super().resolve(**kwargs)

    authority = RevokedCatalogAuthority()
    service = OrganizationCategoryResearchService(
        source_catalog_authority=authority,  # type: ignore[arg-type]
        source_catalog_context=_MaterializingCatalogContext(authority),  # type: ignore[arg-type]
        task_reader=lambda _task_id: _catalog_task(),
        uow_factory=lambda: PlanningControlUnitOfWork(
            session_factory=lambda: Session(engine)
        ),
    )
    binding = {
        "catalog_task_id": "catalog-task-1",
        "catalog_id": "catalog-1",
        "catalog_hash": _CATALOG_HASH,
        "repository_revision": _REVISION,
        "manifest_hash": _MANIFEST_HASH,
        "source_allowlist_version": _CATALOG_HASH,
        "source_scope": "organization:org-1",
    }

    with pytest.raises(SourceCatalogAuthorityError, match="source_catalog_not_current"):
        service.create_task(
            context=_context(),
            goal_id="goal-1",
            unit_id="unit-1",
            team_id="team-1",
            role_slot_id="slot-1",
            catalog_binding=binding,
            idempotency_key="research-catalog-key-1",
        )

    with Session(engine) as session:
        assert list(session.exec(select(TaskDB)).all()) == []
