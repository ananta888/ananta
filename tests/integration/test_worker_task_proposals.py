from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from agent.services.worker_result_capability_service import (
    WorkerResultCapabilityError,
    WorkerResultCapabilityService,
)
from agent.services.worker_task_proposal_contract_service import (
    WorkerTaskProposalContractService,
    proposal_payload_digest,
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
        unit_id="delivery-stream",
        team_id="delivery-a",
        role_slot_id="developer-slot",
        assignment_id="assignment-a",
        dispatch_lease_id="lease-a",
        worker_id="worker-a",
        role_template_ref="backend_engineer@1",
        source_task_status="in_progress",
        lease_active=True,
        allowed_task_kinds=frozenset({"coding", "testing"}),
        allowed_capabilities=frozenset({"coding", "testing"}),
        allowed_context_refs=frozenset({"repository-a"}),
        allowed_evidence_refs=frozenset(),
        source_category_item_ids=frozenset({"category-item-a"}),
        known_role_refs=frozenset({"backend_engineer@1", "qa_engineer@1"}),
        known_team_refs=frozenset({"delivery-a", "quality-a"}),
        known_agent_refs=frozenset({"worker-a", "worker-b"}),
        remaining_budget={
            "estimated_tokens": 10000,
            "estimated_seconds": 3600,
            "estimated_cost_units": 100,
        },
    )


def _envelope(policy: dict) -> dict:
    payload = {
        "title": "Add a regression test",
        "description": "The delegated implementation exposed an uncovered branch.",
        "task_kind": "testing",
        "rationale": "Protect the accepted behavior from regression.",
        "acceptance_criteria": ["The uncovered branch is exercised."],
        "expected_outputs": ["test_report"],
        "dependency_refs": ["task-a"],
        "required_capabilities": ["testing"],
        "suggested_role_refs": ["qa_engineer@1"],
        "suggested_team_refs": ["quality-a"],
        "suggested_agent_refs": ["worker-b"],
        "context_refs": ["repository-a"],
        "evidence_refs": [],
        "risk": "medium",
        "priority_hint": "P1",
        "budget_estimate": {
            "estimated_tokens": 1000,
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
        "source_category_item_ids": ["category-item-a"],
        "organization_id": "organization-a",
        "unit_id": "delivery-stream",
        "team_id": "delivery-a",
        "role_slot_id": "developer-slot",
        "assignment_id": "assignment-a",
        "dispatch_lease_id": "lease-a",
        "proposing_role_template_ref": "backend_engineer@1",
        "proposal_policy_hash": effective_proposal_policy_hash(policy),
        "payload_digest": proposal_payload_digest(payload),
        "proposal_state": "submitted",
        "payload": payload,
    }


@pytest.mark.integration
def test_valid_worker_proposal_is_only_a_non_authoritative_hint() -> None:
    policy = _policy()
    envelope = _envelope(policy)

    contract = WorkerTaskProposalContractService().validate(envelope)
    decision = WorkerTaskProposalPolicyService().evaluate(
        envelope=envelope,
        policy=policy,
        assignment=_assignment(),
        proposal_count=0,
    )

    assert contract["valid"] is True
    assert decision["allowed"] is True
    assert envelope["payload"]["suggested_agent_refs"] == ["worker-b"]
    assert "selected_agent_id" not in envelope
    assert "queue" not in envelope


@pytest.mark.integration
def test_stale_lease_capability_and_capability_escalation_fail_closed() -> None:
    policy = _policy()
    envelope = _envelope(policy)
    envelope["payload"]["required_capabilities"] = ["policy_mutation"]
    envelope["payload_digest"] = proposal_payload_digest(envelope["payload"])
    stale = replace(_assignment(), lease_active=False)

    decision = WorkerTaskProposalPolicyService().evaluate(
        envelope=envelope,
        policy=policy,
        assignment=stale,
        proposal_count=0,
    )

    assert decision["allowed"] is False
    assert "proposal_dispatch_lease_inactive" in decision["issues"]
    assert "proposal_capability_escalation" in decision["issues"]


def test_result_capability_is_bound_to_one_task_and_assignment() -> None:
    service = WorkerResultCapabilityService(signing_secret="test-signing-secret-value")
    token = service.issue(
        worker_id="worker-a",
        source_task_id="task-a",
        assignment_id="assignment-a",
        dispatch_lease_id="lease-a",
    )

    claims = service.verify(
        token,
        source_task_id="task-a",
        assignment_id="assignment-a",
    )
    assert claims["dispatch_lease_id"] == "lease-a"

    with pytest.raises(WorkerResultCapabilityError):
        service.verify(
            token,
            source_task_id="task-other",
            assignment_id="assignment-a",
        )
