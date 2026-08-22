from __future__ import annotations

import copy

import pytest

from agent.services.planning_category_contract_service import (
    PlanningCategoryContractService,
)
from agent.services.planning_evidence_resolver_service import (
    AssignmentEvidenceContext,
)
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


def _category_fixture() -> tuple[dict, AssignmentEvidenceContext, dict]:
    catalog_hash = "a" * 64
    payload = {
        "version": 1,
        "created": "test",
        "updated": "test",
        "project": "test-project",
        "review_basis": {
            "reviewed_commit_range": "test-only",
            "review_goal": "contract validation",
        },
        "categories": [
            {
                "name": "research",
                "label": "Research",
                "items": [
                    {
                        "id": "ITEM-1",
                        "title": "Grounded item",
                        "status": "open",
                        "priority": "P1",
                        "risk": "medium",
                        "type": "research",
                        "depends_on": [],
                        "acceptance_criteria": ["Evidence is verified"],
                        "evidence_claim_refs": ["CLM_0001"],
                    }
                ],
            }
        ],
        "meta": {
            "total_items": 99,
            "by_status": {"completed": 0, "partial": 0, "open": 99},
            "notes": [],
            "recommended_order": [],
        },
        "planning_quality_profile": {
            "schema": "category_todo_quality_profile.v1",
            "source_catalog_id": "catalog-test",
            "source_catalog_hash": catalog_hash,
            "allowed_source_refs": [],
            "allowed_run_refs": [],
            "research_summary": "Test-only grounded research.",
            "claims": [
                {
                    "claim_id": "CLM_0001",
                    "text": "The injected source supports the test claim.",
                    "claim_type": "source_fact",
                    "citation_refs": [],
                    "confidence": "unverified",
                }
            ],
            "unsupported_notes": [],
            "grounding_status": "unverified",
            "grounding_reason": "no_assignment_bound_source_was_provided",
        },
    }
    context = AssignmentEvidenceContext(
        task_id="task-test",
        assignment_id="assignment-test",
        dispatch_lease_id="lease-test",
        tenant_id="tenant-test",
        scope="test",
        source_catalog_id="catalog-test",
        source_catalog_hash=catalog_hash,
        allowed_source_refs=frozenset(),
        allowed_run_refs=frozenset(),
        artifact_hashes={},
    )
    catalog = {
        "source_catalog_id": "catalog-test",
        "source_catalog_hash": catalog_hash,
        "sources": [],
    }
    return payload, context, catalog


def test_category_contract_recomputes_caches_and_fails_closed_without_grounding() -> None:
    payload, context, catalog = _category_fixture()

    result = PlanningCategoryContractService().validate_and_recompute(
        payload,
        evidence_context=context,
        source_catalog=catalog,
        tool_run_catalog=[],
    )

    assert result["promotable"] is False
    assert result["payload"]["meta"]["total_items"] == 1
    assert result["payload"]["meta"]["recommended_order"] == ["ITEM-1"]
    assert result["grounding"]["reason"] == "invalid_grounded_answer_schema"


def test_category_contract_replaces_worker_binding_with_hub_authority() -> None:
    payload, context, catalog = _category_fixture()
    payload["planning_quality_profile"].update(
        {
            "source_catalog_id": "worker-catalog",
            "source_catalog_hash": "f" * 64,
            "allowed_source_refs": ["SRC_9999"],
            "allowed_run_refs": ["RUN_9999"],
        }
    )
    authoritative = AssignmentEvidenceContext(
        task_id=context.task_id,
        assignment_id=context.assignment_id,
        dispatch_lease_id=context.dispatch_lease_id,
        tenant_id=context.tenant_id,
        scope=context.scope,
        source_catalog_id="catalog-authoritative",
        source_catalog_hash="b" * 64,
        allowed_source_refs=frozenset({"SRC_0001"}),
        allowed_run_refs=frozenset({"RUN_0001"}),
        artifact_hashes={},
    )
    catalog = {
        "source_catalog_id": "catalog-authoritative",
        "source_catalog_hash": "b" * 64,
        "sources": [],
    }

    result = PlanningCategoryContractService().validate_and_recompute(
        payload,
        evidence_context=authoritative,
        source_catalog=catalog,
        tool_run_catalog=[],
    )

    quality = result["payload"]["planning_quality_profile"]
    assert quality["source_catalog_id"] == "catalog-authoritative"
    assert quality["source_catalog_hash"] == "b" * 64
    assert quality["allowed_source_refs"] == ["SRC_0001"]
    assert quality["allowed_run_refs"] == ["RUN_0001"]


def _proposal_policy() -> dict:
    policy = WorkerTaskProposalPolicyService.default_deny_policy()
    policy.update(
        {
            "key": "developer_followups",
            "may_propose_tasks": True,
            "allowed_task_kinds": ["coding"],
            "target_scope": ["same_team"],
            "max_proposals_per_source_task": 2,
            "max_amendment_depth": 2,
            "budget_limits": {
                "max_estimated_tokens": 1000,
                "max_estimated_seconds": 60,
                "max_estimated_cost_units": 2,
            },
            "approval_policy": {
                "mode": "human_required",
                "self_approval_allowed": False,
                "materialization_owner": "hub",
            },
        }
    )
    return policy


def _proposal_envelope(policy: dict) -> dict:
    payload = {
        "title": "Add a focused unit test",
        "description": "Add one bounded test for the delegated behavior.",
        "task_kind": "coding",
        "rationale": "The delegated result exposed a missing test.",
        "acceptance_criteria": ["The focused test passes"],
        "expected_outputs": ["test_patch"],
        "dependency_refs": [],
        "required_capabilities": ["code_edit"],
        "suggested_role_refs": [],
        "suggested_team_refs": [],
        "suggested_agent_refs": [],
        "context_refs": ["context:test"],
        "evidence_refs": [],
        "risk": "low",
        "priority_hint": "P2",
        "budget_estimate": {
            "estimated_tokens": 100,
            "estimated_seconds": 10,
            "estimated_cost_units": 1,
        },
    }
    return {
        "schema": "task_followup_proposal.v1",
        "proposal_id": "proposal-test",
        "idempotency_key": "proposal-test-key",
        "source_goal_id": "goal-test",
        "source_task_id": "task-test",
        "source_category_item_ids": ["ITEM-1"],
        "organization_id": "organization-test",
        "unit_id": "unit-test",
        "team_id": "team-test",
        "role_slot_id": "slot-test",
        "assignment_id": "assignment-test",
        "dispatch_lease_id": "lease-test",
        "proposing_role_template_ref": "developer@1",
        "proposal_policy_hash": effective_proposal_policy_hash(policy),
        "payload_digest": proposal_payload_digest(payload),
        "proposal_state": "submitted",
        "payload": payload,
    }


def _assignment() -> AssignmentProposalScope:
    return AssignmentProposalScope(
        tenant_id="tenant-test",
        project_id="project-test",
        organization_id="organization-test",
        goal_id="goal-test",
        source_task_id="task-test",
        unit_id="unit-test",
        team_id="team-test",
        role_slot_id="slot-test",
        assignment_id="assignment-test",
        dispatch_lease_id="lease-test",
        worker_id="worker-test",
        role_template_ref="developer@1",
        source_task_status="in_progress",
        lease_active=True,
        allowed_task_kinds=frozenset({"coding"}),
        allowed_capabilities=frozenset({"code_edit"}),
        allowed_context_refs=frozenset({"context:test"}),
        allowed_evidence_refs=frozenset(),
        source_category_item_ids=frozenset({"ITEM-1"}),
        known_role_refs=frozenset({"developer@1"}),
        known_team_refs=frozenset({"team-test"}),
        known_agent_refs=frozenset(),
        remaining_budget={
            "estimated_tokens": 1000,
            "estimated_seconds": 60,
            "estimated_cost_units": 2,
        },
    )


def test_proposal_contract_and_policy_are_closed_and_restrict_only() -> None:
    policy = _proposal_policy()
    envelope = _proposal_envelope(policy)
    contract = WorkerTaskProposalContractService().validate(envelope)
    assert contract["valid"] is True
    assert contract["envelope_digest"].startswith("sha256:")

    allowed = WorkerTaskProposalPolicyService().evaluate(
        envelope=envelope,
        policy=policy,
        assignment=_assignment(),
        proposal_count=0,
    )
    assert allowed["allowed"] is True

    escalated = copy.deepcopy(envelope)
    escalated["payload"]["required_capabilities"] = ["admin"]
    escalated["payload_digest"] = proposal_payload_digest(escalated["payload"])
    denied = WorkerTaskProposalPolicyService().evaluate(
        envelope=escalated,
        policy=policy,
        assignment=_assignment(),
        proposal_count=0,
    )
    assert denied["allowed"] is False
    assert "proposal_capability_escalation" in denied["issues"]


def test_worker_result_capability_is_bound_to_task_and_assignment() -> None:
    service = WorkerResultCapabilityService(signing_secret="test-signing-secret-long")
    token = service.issue(
        worker_id="worker-test",
        source_task_id="task-test",
        assignment_id="assignment-test",
        dispatch_lease_id="lease-test",
    )
    claims = service.verify(
        token,
        source_task_id="task-test",
        assignment_id="assignment-test",
    )
    assert claims["dispatch_lease_id"] == "lease-test"
    with pytest.raises(WorkerResultCapabilityError):
        service.verify(
            token,
            source_task_id="different-task",
            assignment_id="assignment-test",
        )
