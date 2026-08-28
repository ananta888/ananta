from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlmodel import Session

from agent.artifacts.goal_artifact_repository import GoalArtifactRepository
from agent.artifacts.goal_artifact_service import GoalArtifactService
from agent.database import engine
from agent.db_models import (
    ArtifactDB,
    ArtifactVersionDB,
    GoalDB,
    OrganizationInstanceDB,
    OrganizationRoleSlotDB,
    OrganizationTeamLinkDB,
    OrganizationUnitDB,
    ProjectDB,
    TeamDB,
)
from agent.repositories.organization_runtime import (
    SqlArtifactVersionReader,
    SqlHandoffStateStore,
    SqlOrganizationBudgetLedger,
    SqlOrganizationEventStore,
    SqlOrganizationWorkflowLoopStore,
)
from agent.services.organization_budget_service import (
    OrganizationBudgetLimit,
    OrganizationBudgetRequest,
    OrganizationBudgetService,
)
from agent.services.organization_event_service import OrganizationEventService


def _scope(db_session) -> tuple[str, str, str]:
    suffix = uuid.uuid4().hex
    tenant_id = f"runtime-tenant-{suffix}"
    project_id = f"runtime-project-{suffix}"
    organization_id = f"runtime-organization-{suffix}"
    db_session.add(
        ProjectDB(
            tenant_id=tenant_id,
            project_id=project_id,
            name="Organization runtime persistence test",
            created_by_subject_id="test-hub",
        )
    )
    db_session.flush()
    db_session.add(
        OrganizationInstanceDB(
            tenant_id=tenant_id,
            project_id=project_id,
            organization_id=organization_id,
            name="Runtime test Organization",
            definition_key="runtime_test",
            definition_version=1,
            definition_revision="definition-revision",
            effective_limit_profile_ref="organization_limits@1",
            effective_limit_profile_revision=1,
            effective_limit_profile_hash="limit-profile-hash",
            composition_mode="standard",
            plan_digest="plan-digest",
            idempotency_key=f"runtime-test-{suffix}",
        )
    )
    db_session.commit()
    return tenant_id, project_id, organization_id


def _session_factory() -> Session:
    return Session(engine)


def _seed_handoff_scope(
    db_session: Session,
    *,
    tenant_id: str,
    project_id: str,
    organization_id: str,
) -> None:
    db_session.add_all(
        [
            TeamDB(id="team-a", name="Producer team", is_active=True),
            TeamDB(id="team-b", name="Consumer team", is_active=True),
        ]
    )
    db_session.flush()
    db_session.add_all(
        [
            OrganizationUnitDB(
                id="unit-a",
                tenant_id=tenant_id,
                project_id=project_id,
                organization_id=organization_id,
                unit_key="unit-a",
                name="Producer unit",
                unit_kind="team",
            ),
            OrganizationUnitDB(
                id="unit-b",
                tenant_id=tenant_id,
                project_id=project_id,
                organization_id=organization_id,
                unit_key="unit-b",
                name="Consumer unit",
                unit_kind="team",
            ),
        ]
    )
    db_session.flush()
    db_session.add_all(
        [
            OrganizationTeamLinkDB(
                tenant_id=tenant_id,
                project_id=project_id,
                organization_id=organization_id,
                unit_id="unit-a",
                team_id="team-a",
            ),
            OrganizationTeamLinkDB(
                tenant_id=tenant_id,
                project_id=project_id,
                organization_id=organization_id,
                unit_id="unit-b",
                team_id="team-b",
            ),
            OrganizationRoleSlotDB(
                id="producer-slot",
                tenant_id=tenant_id,
                project_id=project_id,
                organization_id=organization_id,
                unit_id="unit-a",
                slot_key="producer",
                role_template_key="producer",
                role_template_version=1,
            ),
            OrganizationRoleSlotDB(
                id="consumer-slot",
                tenant_id=tenant_id,
                project_id=project_id,
                organization_id=organization_id,
                unit_id="unit-b",
                slot_key="consumer",
                role_template_key="consumer",
                role_template_version=1,
            ),
        ]
    )
    db_session.flush()
    db_session.add(
        GoalDB(
            id="goal-a",
            tenant_id=tenant_id,
            project_id=project_id,
            organization_id=organization_id,
            unit_id="unit-a",
            team_id="team-a",
            goal="Exercise persisted handoff lifecycle",
        )
    )
    db_session.commit()


def test_sql_budget_ledger_reserves_replays_and_settles(db_session) -> None:
    tenant_id, project_id, organization_id = _scope(db_session)
    ledger = SqlOrganizationBudgetLedger(
        tenant_id=tenant_id,
        project_id=project_id,
        organization_id=organization_id,
        session_factory=_session_factory,
    )
    service = OrganizationBudgetService(ledger=ledger)
    request = OrganizationBudgetRequest(
        reservation_id="reservation-a",
        organization_id=organization_id,
        unit_id=None,
        team_id=None,
        workflow_id=None,
        task_id="task-a",
        tokens=100,
        cost=Decimal("1.25"),
        wall_seconds=30,
        parallel_slots=1,
        model_profile="bounded",
    )
    limits = (
        OrganizationBudgetLimit(
            "organization",
            organization_id,
            1_000,
            Decimal("10"),
            300,
            2,
            "1",
        ),
        OrganizationBudgetLimit(
            "task",
            "task-a",
            200,
            Decimal("2"),
            60,
            1,
            "1",
        ),
    )

    first = service.reserve(request=request, limits=limits)
    replay = service.reserve(request=request, limits=limits)
    settled = service.settle(
        reservation_id=request.reservation_id,
        actual_tokens=80,
        actual_cost="1.00",
        actual_wall_seconds=20,
    )

    assert first.allowed is True
    assert replay.replayed is True
    assert settled is True
    assert ledger.usage()[f"task:{request.task_id}"].parallel_slots == 0


def test_sql_budget_ledger_persists_denial_for_exact_replay(db_session) -> None:
    tenant_id, project_id, organization_id = _scope(db_session)
    ledger = SqlOrganizationBudgetLedger(
        tenant_id=tenant_id,
        project_id=project_id,
        organization_id=organization_id,
        session_factory=_session_factory,
    )
    service = OrganizationBudgetService(ledger=ledger)
    request = OrganizationBudgetRequest(
        reservation_id="reservation-denied",
        organization_id=organization_id,
        unit_id=None,
        team_id=None,
        workflow_id=None,
        task_id="task-denied",
        tokens=101,
        cost=Decimal("1.01"),
        wall_seconds=31,
        parallel_slots=1,
        model_profile="bounded",
    )
    limits = (
        OrganizationBudgetLimit(
            "organization",
            organization_id,
            100,
            Decimal("1"),
            30,
            1,
            "1",
        ),
    )

    first = service.reserve(request=request, limits=limits)
    replay = service.reserve(request=request, limits=limits)

    assert first.allowed is False
    assert replay.allowed is False
    assert replay.replayed is True
    assert replay.exceeded_scopes == first.exceeded_scopes
    assert ledger.usage()[f"organization:{organization_id}"].tokens == 0


def test_sql_event_store_replays_one_redacted_event(db_session) -> None:
    tenant_id, project_id, organization_id = _scope(db_session)
    store = SqlOrganizationEventStore(
        tenant_id=tenant_id,
        project_id=project_id,
        organization_id=organization_id,
        session_factory=_session_factory,
    )
    service = OrganizationEventService(store=store)
    values = {
        "event_type": "team_started",
        "organization_id": organization_id,
        "definition_revision": "definition-revision",
        "snapshot_hash": "snapshot-hash",
        "correlation_id": "correlation-a",
        "idempotency_key": "event-idempotency-a",
        "payload": {"team_id": "team-a", "agent_token": "secret"},
    }

    first = service.emit(**values)
    replay = service.emit(**values)
    projection = service.runtime_projection(organization_id=organization_id)

    assert first.event_id == replay.event_id
    assert first.sequence == 1
    assert projection["replayed_event_count"] == 1
    assert first.payload["agent_token"].startswith("***REDACTED")


def test_sql_handoff_store_cas_and_lifecycle_resolution(db_session) -> None:
    tenant_id, project_id, organization_id = _scope(db_session)
    _seed_handoff_scope(
        db_session,
        tenant_id=tenant_id,
        project_id=project_id,
        organization_id=organization_id,
    )
    store = SqlHandoffStateStore(
        tenant_id=tenant_id,
        project_id=project_id,
        organization_id=organization_id,
        session_factory=_session_factory,
    )
    contract = {
        "handoff_id": "handoff-a",
        "correlation_id": "correlation-a",
        "organization_id": organization_id,
        "goal_id": "goal-a",
        "producer_unit_id": "unit-a",
        "producer_team_id": "team-a",
        "producer_role_slot_id": "producer-slot",
        "producer_task_id": "producer-task",
        "consumer_unit_id": "unit-b",
        "consumer_team_id": "team-b",
        "consumer_role_slot_id": "consumer-slot",
        "consumer_task_id": "consumer-task",
        "artifact_refs": [],
        "acceptance_checks": ["verified"],
        "due_at": "2030-01-01T00:00:00Z",
        "sla_seconds": 3600,
    }

    inserted = store.save_if_revision(
        "handoff-a",
        0,
        {
            "contract": contract,
            "status": "pending_acceptance",
            "reason_code": "handoff_submitted",
            "revision": 1,
            "idempotency_key": "handoff-submit-a",
            "artifact_digests": ["digest-a"],
        },
    )
    resolved = store.resolve_open(
        resolution="cancelled",
        reason_code="organization_archived",
        actor_principal_id="hub",
        idempotency_key="lifecycle-cancel-a",
    )

    assert inserted is True
    assert resolved == ("handoff-a",)
    assert store.get("handoff-a")["status"] == "cancelled"


def test_sql_workflow_loop_store_rejects_stale_revision(db_session) -> None:
    tenant_id, project_id, organization_id = _scope(db_session)
    store = SqlOrganizationWorkflowLoopStore(
        tenant_id=tenant_id,
        project_id=project_id,
        organization_id=organization_id,
        session_factory=_session_factory,
    )
    inserted, state = store.create_once(
        {
            "loop_instance_id": "loop-instance-a",
            "loop_id": "quality-rework",
            "definition_revision": "definition-revision",
            "snapshot_hash": "snapshot-hash",
            "policy": {
                "loop_id": "quality-rework",
                "source_phase": "quality",
                "target_phase": "delivery",
                "max_iterations": 2,
                "timeout_seconds": 60,
                "exit_condition": "quality_passed",
                "on_exhausted_policy": "human_escalation",
            },
            "status": "running",
            "started_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
            "last_idempotency_key": "loop-create-a",
            "last_request_digest": "create-digest",
        }
    )

    stale = store.save_if_revision(
        loop_instance_id="loop-instance-a",
        expected_revision=2,
        value={
            **state,
            "status": "rework_requested",
            "last_idempotency_key": "loop-transition-a",
            "last_request_digest": "transition-digest",
        },
    )

    assert inserted is True
    assert stale is False
    assert store.get("loop-instance-a")["revision"] == 1


def test_artifact_reader_requires_goal_graph_and_sql_digest_agreement(
    db_session,
    tmp_path,
) -> None:
    digest = "a" * 64
    db_session.add(
        ArtifactDB(
            id="artifact-handoff-a",
            latest_version_id="artifact-version-handoff-a",
            latest_sha256=digest,
            status="stored",
        )
    )
    db_session.add(
        ArtifactVersionDB(
            id="artifact-version-handoff-a",
            artifact_id="artifact-handoff-a",
            version_number=1,
            storage_path="artifact-handoff-a/report.json",
            original_filename="report.json",
            media_type="application/json",
            size_bytes=2,
            sha256=digest,
        )
    )
    db_session.commit()
    goal_artifacts = GoalArtifactService(
        repository=GoalArtifactRepository(root=tmp_path),
    )
    goal_artifacts.record_output_artifact(
        goal_id="goal-handoff-a",
        output_artifact={
            "output_artifact_id": "output-handoff-a",
            "goal_id": "goal-handoff-a",
            "artifact_type": "report",
            "created_at": datetime.now(UTC).isoformat(),
            "artifact_ref": "artifact-handoff-a",
            "content_hash": digest,
            "status": "verified",
            "provenance_summary": "Hub-verified test artifact",
            "extensions": {
                "evidence_refs": [],
                "context_scope_refs": ["context:handoff-a"],
            },
        },
    )
    reader = SqlArtifactVersionReader(
        session_factory=_session_factory,
        goal_artifacts=goal_artifacts,
    )

    result = reader.get_verified_version(
        goal_id="goal-handoff-a",
        artifact_id="artifact-handoff-a",
        version="artifact-version-handoff-a",
    )

    assert result is not None
    assert result.digest == f"sha256:{digest}"
    assert result.evidence_refs == ()
