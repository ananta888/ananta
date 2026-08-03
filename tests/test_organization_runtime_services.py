from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from agent.ports.artifact_handoff import VerifiedArtifactVersion
from agent.services.organization_budget_service import (
    InMemoryOrganizationBudgetLedger,
    OrganizationBudgetLimit,
    OrganizationBudgetRequest,
    OrganizationBudgetService,
)
from agent.services.organization_dependency_service import (
    OrganizationDependencyService,
    OrganizationTaskDependency,
    OrganizationTaskRef,
)
from agent.services.organization_event_service import (
    InMemoryOrganizationEventStore,
    OrganizationEventService,
)
from agent.services.organization_lifecycle_service import (
    OrganizationActivitySnapshot,
    OrganizationLifecycleService,
)
from agent.services.organization_reconciliation_service import OrganizationReconciliationService
from agent.services.organization_workflow_loop_service import (
    OrganizationLoopPolicy,
    OrganizationLoopState,
    OrganizationWorkflowLoopService,
)
from agent.services.separation_of_duties_service import DutyAssignment, SeparationOfDutiesPolicy
from agent.services.team_handoff_service import (
    InMemoryHandoffStateStore,
    TeamHandoffArtifactRef,
    TeamHandoffContract,
    TeamHandoffService,
)


def _dependency(identifier: str, source: str, target: str) -> OrganizationTaskDependency:
    return OrganizationTaskDependency(
        dependency_id=identifier,
        organization_id="org-1",
        source_task_id=source,
        source_team_id="team-a",
        target_task_id=target,
        target_team_id="team-b",
        owner_role_slot_id="portfolio-slot",
        gate_id=None,
        required_artifact_refs=(),
        due_at=None,
        status="pending",
        blocking_reason=None,
        escalation_rule="portfolio-escalation",
    )


def test_dependency_service_detects_cross_team_cycle() -> None:
    tasks = (
        OrganizationTaskRef("task-a", "team-a", "todo"),
        OrganizationTaskRef("task-b", "team-b", "todo"),
    )
    result = OrganizationDependencyService().validate(
        tasks=tasks,
        dependencies=(
            _dependency("dep-a", "task-a", "task-b"),
            replace(
                _dependency("dep-b", "task-b", "task-a"),
                source_team_id="team-b",
                target_team_id="team-a",
            ),
        ),
    )

    assert result.valid is False
    assert "dependency_cycle_detected" in result.reason_codes


def test_dependency_release_requires_completed_source_and_verified_artifact() -> None:
    dependency = replace(
        _dependency("dep-a", "task-a", "task-b"),
        required_artifact_refs=("artifact-version-ref",),
        gate_id="quality-gate",
    )
    tasks = {
        "task-a": OrganizationTaskRef("task-a", "team-a", "completed"),
        "task-b": OrganizationTaskRef("task-b", "team-b", "todo"),
    }

    denied = OrganizationDependencyService().releasable(
        target_task_id="task-b",
        tasks=tasks,
        dependencies=(dependency,),
    )
    allowed = OrganizationDependencyService().releasable(
        target_task_id="task-b",
        tasks=tasks,
        dependencies=(dependency,),
        verified_artifact_refs={"artifact-version-ref"},
        satisfied_gate_ids={"quality-gate"},
    )

    assert denied[0] is False
    assert allowed == (True, ())


class _ArtifactReader:
    def get_verified_version(self, *, goal_id: str, artifact_id: str, version: str):
        return VerifiedArtifactVersion(artifact_id, version, "digest-1", "verified", (), ("handoff",))


class _EvidenceVerifier:
    def verify(self, **kwargs):
        return True, ()


def test_handoff_is_idempotent_and_consumer_decision_is_structured() -> None:
    service = TeamHandoffService(
        artifacts=_ArtifactReader(),
        evidence=_EvidenceVerifier(),
        store=InMemoryHandoffStateStore(),
    )
    contract = TeamHandoffContract(
        handoff_id="handoff-1",
        correlation_id="correlation-1",
        organization_id="org-1",
        goal_id="goal-1",
        producer_unit_id="unit-a",
        producer_team_id="team-a",
        producer_role_slot_id="developer-slot",
        producer_task_id="task-a",
        consumer_unit_id="unit-b",
        consumer_team_id="team-b",
        consumer_role_slot_id="review-slot",
        consumer_task_id="task-b",
        artifact_refs=(TeamHandoffArtifactRef("artifact-1", "1", "digest-1"),),
        acceptance_checks=("independent_review",),
        due_at="2030-01-01T00:00:00Z",
        sla_seconds=3600,
    )

    first = service.submit(
        contract=contract,
        assignment_id="assignment-a",
        dispatch_lease_id="lease-a",
        idempotency_key="key-a",
    )
    replay = service.submit(
        contract=contract,
        assignment_id="assignment-a",
        dispatch_lease_id="lease-a",
        idempotency_key="key-a",
    )
    accepted = service.decide(
        handoff_id="handoff-1",
        decision="accepted",
        reason_code="all_checks_passed",
        actor_principal_id="reviewer",
        expected_revision=1,
        idempotency_key="decision-a",
        duty_assignments=(DutyAssignment("reviewer", "review-slot", "team-b", frozenset({"independent_reviewer"})),),
        sod_policy=SeparationOfDutiesPolicy.enterprise_default(),
    )
    decision_replay = service.decide(
        handoff_id="handoff-1",
        decision="accepted",
        reason_code="all_checks_passed",
        actor_principal_id="reviewer",
        expected_revision=1,
        idempotency_key="decision-a",
        duty_assignments=(DutyAssignment("reviewer", "review-slot", "team-b", frozenset({"independent_reviewer"})),),
        sod_policy=SeparationOfDutiesPolicy.enterprise_default(),
    )

    assert first.status == replay.status == "pending_acceptance"
    assert accepted.status == "accepted"
    assert accepted.revision == 2
    assert decision_replay.replayed is True
    assert decision_replay.revision == 2


def test_budget_reservation_is_atomic_idempotent_and_bounded() -> None:
    service = OrganizationBudgetService(ledger=InMemoryOrganizationBudgetLedger())
    limits = (
        OrganizationBudgetLimit("organization", "org-1", 1_000, Decimal("10"), 600, 2, "1"),
        OrganizationBudgetLimit("task", "task-1", 500, Decimal("5"), 300, 1, "1"),
    )
    request = OrganizationBudgetRequest(
        reservation_id="reservation-1",
        organization_id="org-1",
        unit_id="unit-1",
        team_id="team-a",
        workflow_id="workflow-1",
        task_id="task-1",
        tokens=400,
        cost=Decimal("4"),
        wall_seconds=200,
        parallel_slots=1,
        model_profile="bounded",
    )

    first = service.reserve(request=request, limits=limits)
    replay = service.reserve(request=request, limits=limits)
    denied = service.reserve(
        request=OrganizationBudgetRequest(
            reservation_id="reservation-2",
            organization_id="org-1",
            unit_id="unit-1",
            team_id="team-a",
            workflow_id="workflow-1",
            task_id="task-1",
            tokens=200,
            cost=Decimal("2"),
            wall_seconds=120,
            parallel_slots=1,
            model_profile="bounded",
        ),
        limits=limits,
    )

    assert first.allowed is True
    assert replay.replayed is True
    assert denied.allowed is False
    assert denied.reason_code == "organization_budget_exhausted"


def test_feedback_loop_exhaustion_escalates_without_dag_edge() -> None:
    service = OrganizationWorkflowLoopService()
    policy = OrganizationLoopPolicy("qa-rework", "quality", "delivery", 2, 60, "checks_pass", "human_escalation")
    state = OrganizationLoopState("qa-rework", 2, "running", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z", "1.0", ())

    decision = service.request_rework(
        policy=policy,
        state=state,
        artifact_version="artifact-version-ref",
        incremental_cost="0.2",
        exit_condition_satisfied=False,
    )

    assert decision.state.status == "escalated"
    assert decision.creates_dependency_edge is False


def test_event_replay_is_idempotent_and_redacted() -> None:
    store = InMemoryOrganizationEventStore()
    service = OrganizationEventService(store=store)
    first = service.emit(
        event_type="team_started",
        organization_id="org-1",
        definition_revision="revision-1",
        snapshot_hash="snapshot-1",
        correlation_id="correlation-1",
        idempotency_key="start-team-a",
        payload={"team_id": "team-a", "agent_token": "must-not-leak"},
    )
    replay = service.emit(
        event_type="team_started",
        organization_id="org-1",
        definition_revision="revision-1",
        snapshot_hash="snapshot-1",
        correlation_id="correlation-1",
        idempotency_key="start-team-a",
        payload={"team_id": "team-a", "agent_token": "must-not-leak"},
    )
    projection = service.replay(
        organization_id="org-1",
        initial={"count": 0},
        reducer=lambda state, event: {"count": int(state["count"]) + 1, "last": event.event_type},
    )

    assert first.event_id == replay.event_id
    assert first.payload["agent_token"].startswith("***REDACTED")
    assert projection == {"count": 1, "last": "team_started"}


def test_lifecycle_and_reconcile_preserve_lineage_and_local_overrides() -> None:
    lifecycle = OrganizationLifecycleService().plan_transition(
        organization_id="org-1",
        current_state="paused",
        target_state="archived",
        activity=OrganizationActivitySnapshot(running_task_ids=("task-1",)),
        active_work_strategy="drain",
    )
    reconcile = OrganizationReconciliationService().plan(
        definition_key="enterprise-medium",
        current_definition={"units": ["old"], "relations": []},
        desired_definition={"units": ["new"], "relations": ["relation"]},
        local_override_paths=("$.units",),
        active_instance_snapshot_revisions=("revision-1",),
    )

    assert lifecycle.allowed is True
    assert "tasks" in lifecycle.preserves_lineage
    assert lifecycle.reruns_tasks is False
    assert reconcile.applicable is False
    assert "local_override_conflict:$.units" in reconcile.blockers
    assert "preserve_active_instance_snapshots" in reconcile.planned_writes
