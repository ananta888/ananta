from __future__ import annotations

from copy import deepcopy

import pytest

from agent.services.worker_task_proposal_contract_service import (
    WorkerTaskProposalContractService,
    proposal_envelope_digest,
    proposal_payload_digest,
)


def _proposal_envelope() -> dict:
    payload = {
        "title": "Add a bounded regression check",
        "description": "The assigned Worker found a branch that needs explicit verification.",
        "task_kind": "testing",
        "rationale": "Keep the accepted Hub-owned behavior stable.",
        "acceptance_criteria": ["The branch is covered without changing orchestration authority."],
        "expected_outputs": ["test_report"],
        "dependency_refs": ["source-task"],
        "required_capabilities": ["testing"],
        "suggested_role_refs": ["qa_engineer@1"],
        "suggested_team_refs": ["quality-team"],
        "suggested_agent_refs": ["candidate-worker"],
        "context_refs": ["assigned-repository"],
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
        "proposal_id": "proposal-one",
        "idempotency_key": "proposal-one-attempt-one",
        "source_goal_id": "goal-one",
        "source_task_id": "source-task",
        "source_category_item_ids": ["category-item-one"],
        "organization_id": "organization-one",
        "unit_id": "delivery-stream",
        "team_id": "delivery-team",
        "role_slot_id": "developer-slot",
        "assignment_id": "assignment-one",
        "dispatch_lease_id": "lease-one",
        "proposing_role_template_ref": "backend_engineer@1",
        "proposal_policy_hash": f"sha256:{'1' * 64}",
        "payload_digest": proposal_payload_digest(payload),
        "proposal_state": "submitted",
        "payload": payload,
    }


def test_contract_accepts_a_digest_bound_non_authoritative_proposal() -> None:
    envelope = _proposal_envelope()

    result = WorkerTaskProposalContractService().validate(envelope)

    assert result["valid"] is True
    assert result["issues"] == []
    assert result["payload_digest"] == envelope["payload_digest"]
    assert result["envelope_digest"] == proposal_envelope_digest(envelope)
    assert "queue" not in envelope
    assert "selected_agent_id" not in envelope


@pytest.mark.parametrize(
    ("location", "field"),
    (("envelope", "queue"), ("payload", "selected_agent_id")),
)
def test_closed_contract_rejects_unknown_authoritative_fields(location: str, field: str) -> None:
    envelope = _proposal_envelope()
    target = envelope if location == "envelope" else envelope["payload"]
    target[field] = "worker-controlled-value"
    envelope["payload_digest"] = proposal_payload_digest(envelope["payload"])

    result = WorkerTaskProposalContractService().validate(envelope)

    assert result["valid"] is False
    assert any(issue["reason_code"] == "worker_task_proposal_schema_invalid" for issue in result["issues"])


def test_payload_tampering_is_detected_without_mutating_the_envelope() -> None:
    envelope = _proposal_envelope()
    original = deepcopy(envelope)
    envelope["payload"]["risk"] = "critical"

    result = WorkerTaskProposalContractService().validate(envelope)

    assert result["valid"] is False
    assert any(issue["reason_code"] == "worker_task_proposal_digest_mismatch" for issue in result["issues"])
    assert original["payload_digest"] == envelope["payload_digest"]


def test_unverified_grounding_placeholder_fails_closed() -> None:
    envelope = _proposal_envelope()
    envelope["payload"]["evidence_refs"] = ["unverified-source"]
    envelope["payload_digest"] = proposal_payload_digest(envelope["payload"])

    result = WorkerTaskProposalContractService().validate(envelope)

    assert result["valid"] is False
    assert any(issue["path"] == "payload/evidence_refs/0" for issue in result["issues"])
