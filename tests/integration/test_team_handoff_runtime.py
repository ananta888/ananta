from __future__ import annotations

import pytest

from agent.ports.artifact_handoff import VerifiedArtifactVersion
from agent.services.separation_of_duties_service import (
    DutyAssignment,
    SeparationOfDutiesPolicy,
)
from agent.services.team_handoff_service import (
    InMemoryHandoffStateStore,
    TeamHandoffArtifactRef,
    TeamHandoffContract,
    TeamHandoffService,
)


class BoundArtifactReader:
    def get_verified_version(self, *, goal_id: str, artifact_id: str, version: str):
        if (goal_id, artifact_id, version) != ("goal-a", "artifact-a", "1"):
            return None
        return VerifiedArtifactVersion(
            artifact_id="artifact-a",
            version="1",
            digest="digest-a",
            verification_status="verified",
            evidence_refs=(),
            context_scope_refs=("handoff-a",),
        )


class AssignmentBoundEvidenceVerifier:
    def verify(self, **kwargs):
        allowed = (
            kwargs["assignment_id"] == "assignment-a"
            and kwargs["dispatch_lease_id"] == "lease-a"
            and tuple(kwargs["context_scope_refs"]) == ("handoff-a",)
        )
        return allowed, () if allowed else ("handoff_evidence_scope_mismatch",)


def _contract() -> TeamHandoffContract:
    return TeamHandoffContract(
        handoff_id="handoff-a",
        correlation_id="correlation-a",
        organization_id="organization-a",
        goal_id="goal-a",
        producer_unit_id="delivery-stream",
        producer_team_id="delivery-a",
        producer_role_slot_id="developer-slot",
        producer_task_id="implementation-task",
        consumer_unit_id="quality-stream",
        consumer_team_id="quality-a",
        consumer_role_slot_id="review-slot",
        consumer_task_id="review-task",
        artifact_refs=(TeamHandoffArtifactRef("artifact-a", "1", "digest-a"),),
        acceptance_checks=("artifact_verified", "independent_review"),
        due_at="2030-01-01T00:00:00Z",
        sla_seconds=3600,
    )


@pytest.mark.integration
def test_handoff_requires_assignment_and_lease_bound_evidence() -> None:
    service = TeamHandoffService(
        artifacts=BoundArtifactReader(),
        evidence=AssignmentBoundEvidenceVerifier(),
        store=InMemoryHandoffStateStore(),
    )

    blocked = service.submit(
        contract=_contract(),
        assignment_id="assignment-a",
        dispatch_lease_id="stale-lease",
        idempotency_key="handoff-submit-stale",
    )
    accepted_for_review = service.submit(
        contract=_contract(),
        assignment_id="assignment-a",
        dispatch_lease_id="lease-a",
        idempotency_key="handoff-submit-valid",
    )

    assert blocked.status == "blocked"
    assert blocked.reason_code == "handoff_evidence_scope_mismatch"
    assert accepted_for_review.status == "pending_acceptance"


@pytest.mark.integration
def test_handoff_rejects_self_review_and_preserves_cas_revision() -> None:
    service = TeamHandoffService(
        artifacts=BoundArtifactReader(),
        evidence=AssignmentBoundEvidenceVerifier(),
        store=InMemoryHandoffStateStore(),
    )
    service.submit(
        contract=_contract(),
        assignment_id="assignment-a",
        dispatch_lease_id="lease-a",
        idempotency_key="handoff-submit-valid",
    )
    assignments = (
        DutyAssignment(
            "same-principal",
            "developer-slot",
            "delivery-a",
            frozenset({"implementer"}),
        ),
    )

    decision = service.decide(
        handoff_id="handoff-a",
        decision="accepted",
        reason_code="review_complete",
        actor_principal_id="same-principal",
        expected_revision=1,
        idempotency_key="handoff-decision",
        duty_assignments=assignments,
        sod_policy=SeparationOfDutiesPolicy.enterprise_default(),
    )

    assert decision.status == "blocked"
    assert decision.reason_code == "sod_principal_collision"
    assert decision.revision == 1
