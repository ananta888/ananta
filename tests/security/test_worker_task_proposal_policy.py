from __future__ import annotations

import copy
import json
from dataclasses import replace
from pathlib import Path

import pytest

from agent.services.planning_artifact_transition_service import (
    PlanningOperationContext,
    PlanningTransitionError,
)
from agent.services.worker_task_proposal_contract_service import (
    WorkerTaskProposalContractService,
    proposal_payload_digest,
)
from agent.services.worker_task_proposal_decision_service import (
    WorkerTaskProposalDecisionService,
)
from agent.services.worker_task_proposal_policy_service import (
    AssignmentProposalScope,
    WorkerTaskProposalPolicyService,
    effective_proposal_policy_hash,
)

ROOT = Path(__file__).resolve().parents[2]


def _policy() -> dict:
    return json.loads(
        (ROOT / "config/blueprints/standard/policies.d/delivery-task-proposals.json").read_text(encoding="utf-8")
    )


def _assignment() -> AssignmentProposalScope:
    return AssignmentProposalScope(
        tenant_id="tenant-a",
        project_id="project-a",
        organization_id="organization-a",
        goal_id="goal-a",
        source_task_id="task-a",
        unit_id="unit-a",
        team_id="team-a",
        role_slot_id="slot-a",
        assignment_id="assignment-a",
        dispatch_lease_id="lease-a",
        worker_id="worker-a",
        role_template_ref="backend_engineer@1",
        source_task_status="in_progress",
        lease_active=True,
        allowed_task_kinds=frozenset({"coding", "testing"}),
        allowed_capabilities=frozenset({"coding", "testing"}),
        allowed_context_refs=frozenset({"context:repository"}),
        allowed_evidence_refs=frozenset(),
        source_category_item_ids=frozenset({"ITEM-A"}),
        known_role_refs=frozenset({"backend_engineer@1", "qa_engineer@1"}),
        known_team_refs=frozenset({"team-a", "team-quality"}),
        known_agent_refs=frozenset({"worker-a", "worker-review"}),
        remaining_budget={
            "estimated_tokens": 10_000,
            "estimated_seconds": 3_600,
            "estimated_cost_units": 100,
        },
        amendment_depth=0,
    )


def _envelope(policy: dict | None = None) -> dict:
    effective_policy = policy or _policy()
    payload = {
        "title": "Add a bounded regression test",
        "description": "The delegated implementation exposed an uncovered branch.",
        "task_kind": "testing",
        "rationale": "Protect the assigned behavior without expanding scope.",
        "acceptance_criteria": ["The assigned behavior is covered."],
        "expected_outputs": ["test_report"],
        "dependency_refs": [],
        "required_capabilities": ["testing"],
        "suggested_role_refs": ["qa_engineer@1"],
        "suggested_team_refs": ["team-quality"],
        "suggested_agent_refs": ["worker-review"],
        "context_refs": ["context:repository"],
        "evidence_refs": [],
        "risk": "medium",
        "priority_hint": "P1",
        "budget_estimate": {
            "estimated_tokens": 1_000,
            "estimated_seconds": 600,
            "estimated_cost_units": 10,
        },
    }
    return {
        "schema": "task_followup_proposal.v1",
        "proposal_id": "proposal-a",
        "idempotency_key": "proposal-a-idempotency",
        "source_goal_id": "goal-a",
        "source_task_id": "task-a",
        "source_category_item_ids": ["ITEM-A"],
        "organization_id": "organization-a",
        "unit_id": "unit-a",
        "team_id": "team-a",
        "role_slot_id": "slot-a",
        "assignment_id": "assignment-a",
        "dispatch_lease_id": "lease-a",
        "proposing_role_template_ref": "backend_engineer@1",
        "proposal_policy_hash": effective_proposal_policy_hash(effective_policy),
        "payload_digest": proposal_payload_digest(payload),
        "proposal_state": "submitted",
        "payload": payload,
    }


def _evaluate(
    envelope: dict,
    *,
    assignment: AssignmentProposalScope | None = None,
    policy: dict | None = None,
    proposal_count: int = 0,
) -> dict:
    return WorkerTaskProposalPolicyService().evaluate(
        envelope=envelope,
        policy=policy or _policy(),
        assignment=assignment or _assignment(),
        proposal_count=proposal_count,
    )


def test_valid_proposal_remains_a_non_authoritative_hint() -> None:
    envelope = _envelope()
    contract = WorkerTaskProposalContractService().validate(envelope)
    decision = _evaluate(envelope)

    assert contract["valid"] is True
    assert decision["allowed"] is True
    assert decision["approval_mode"] == "hub_policy"
    assert "selected_role_slot_id" not in envelope
    assert "selected_team_id" not in envelope
    assert "selected_agent_id" not in envelope
    assert "queue" not in envelope


@pytest.mark.parametrize(
    ("field", "value", "reason_code"),
    [
        ("source_goal_id", "goal-other", "proposal_source_goal_id_mismatch"),
        ("source_task_id", "task-other", "proposal_source_task_id_mismatch"),
        ("organization_id", "organization-other", "proposal_organization_id_mismatch"),
        ("unit_id", "unit-other", "proposal_unit_id_mismatch"),
        ("team_id", "team-other", "proposal_team_id_mismatch"),
        ("role_slot_id", "slot-other", "proposal_role_slot_id_mismatch"),
        ("assignment_id", "assignment-other", "proposal_assignment_id_mismatch"),
        ("dispatch_lease_id", "lease-other", "proposal_dispatch_lease_id_mismatch"),
    ],
)
def test_every_assignment_scope_field_is_exact(
    field: str,
    value: str,
    reason_code: str,
) -> None:
    envelope = _envelope()
    envelope[field] = value

    decision = _evaluate(envelope)

    assert decision["allowed"] is False
    assert reason_code in decision["issues"]


def test_inactive_or_reassigned_lease_fails_closed() -> None:
    inactive = replace(_assignment(), lease_active=False)
    reassigned = replace(_assignment(), dispatch_lease_id="lease-current")

    inactive_result = _evaluate(_envelope(), assignment=inactive)
    reassigned_result = _evaluate(_envelope(), assignment=reassigned)

    assert "proposal_dispatch_lease_inactive" in inactive_result["issues"]
    assert "proposal_dispatch_lease_id_mismatch" in reassigned_result["issues"]
    assert inactive_result["allowed"] is False
    assert reassigned_result["allowed"] is False


@pytest.mark.parametrize(
    ("mutate", "reason_code"),
    [
        (
            lambda row: row["payload"].update(required_capabilities=["policy_mutation"]),
            "proposal_capability_escalation",
        ),
        (lambda row: row["payload"].update(context_refs=["context:foreign"]), "proposal_context_escalation"),
        (lambda row: row.update(source_category_item_ids=["ITEM-OUTSIDE"]), "proposal_category_scope_expansion"),
        (lambda row: row["payload"].update(task_kind="deployment"), "proposal_task_kind_forbidden"),
        (
            lambda row: row["payload"].update(
                budget_estimate={
                    "estimated_tokens": 999_999,
                    "estimated_seconds": 600,
                    "estimated_cost_units": 10,
                }
            ),
            "proposal_budget_policy_exceeded:estimated_tokens",
        ),
    ],
)
def test_policy_is_restrict_only(mutate, reason_code: str) -> None:
    envelope = _envelope()
    mutate(envelope)
    envelope["payload_digest"] = proposal_payload_digest(envelope["payload"])

    decision = _evaluate(envelope)

    assert decision["allowed"] is False
    assert reason_code in decision["issues"]


def test_policy_hash_count_and_amendment_depth_are_revalidated() -> None:
    stale = _envelope()
    stale["proposal_policy_hash"] = "sha256:" + "0" * 64

    stale_result = _evaluate(stale)
    count_result = _evaluate(_envelope(), proposal_count=int(_policy()["max_proposals_per_source_task"]))
    depth_result = _evaluate(
        _envelope(),
        assignment=replace(_assignment(), amendment_depth=int(_policy()["max_amendment_depth"])),
    )

    assert "proposal_policy_hash_stale" in stale_result["issues"]
    assert "proposal_count_limit_exceeded" in count_result["issues"]
    assert "proposal_amendment_depth_exceeded" in depth_result["issues"]
    assert not stale_result["allowed"] and not count_result["allowed"] and not depth_result["allowed"]


def test_direct_worker_address_and_unknown_target_are_rejected() -> None:
    envelope = _envelope()
    envelope["payload"]["suggested_agent_refs"] = ["https://worker.invalid/direct"]
    envelope["payload_digest"] = proposal_payload_digest(envelope["payload"])

    decision = _evaluate(envelope)

    assert decision["allowed"] is False
    assert "proposal_suggested_agent_refs_unknown" in decision["issues"]
    assert "proposal_direct_worker_address_forbidden" in decision["issues"]


def test_missing_policy_defaults_to_deny() -> None:
    envelope = _envelope()
    result = WorkerTaskProposalPolicyService().evaluate(
        envelope=envelope,
        policy=None,
        assignment=_assignment(),
        proposal_count=0,
    )

    assert result["allowed"] is False
    assert "proposal_policy_default_deny" in result["issues"]
    assert "proposal_policy_hash_stale" in result["issues"]


def test_contract_is_closed_against_self_approval_and_routing_claims() -> None:
    policy = _policy()
    assert policy["approval_policy"]["self_approval_allowed"] is False
    envelope = _envelope(policy)
    envelope["approved_by"] = "worker-a"
    envelope["selected_agent_id"] = "worker-a"

    contract = WorkerTaskProposalContractService().validate(envelope)

    assert contract["valid"] is False
    assert any(issue["reason_code"] == "worker_task_proposal_schema_invalid" for issue in contract["issues"])


def test_worker_context_cannot_classify_or_reject_proposals() -> None:
    worker_context = PlanningOperationContext(
        subject_id="worker-a",
        tenant_id="tenant-a",
        project_id="project-a",
        organization_id="organization-a",
        hub_owned=False,
        roles=frozenset(),
    )
    unprivileged_hub_context = replace(worker_context, subject_id="user-a", hub_owned=True)

    with pytest.raises(PlanningTransitionError, match="planning_hub_authority_required"):
        WorkerTaskProposalDecisionService._authorize(worker_context)
    with pytest.raises(PlanningTransitionError, match="proposal_classification_authority_required"):
        WorkerTaskProposalDecisionService._authorize(unprivileged_hub_context)


def test_envelope_mutation_without_digest_update_is_detected_by_contract() -> None:
    envelope = _envelope()
    tampered = copy.deepcopy(envelope)
    tampered["payload"]["description"] = "Changed after the Worker signed the payload."

    contract = WorkerTaskProposalContractService().validate(tampered)

    assert contract["valid"] is False
    assert any(issue["reason_code"] == "worker_task_proposal_digest_mismatch" for issue in contract["issues"])
