from __future__ import annotations

import pytest

from agent.services.planning_category_contract_service import (
    PlanningCategoryContractService,
)
from agent.services.planning_evidence_resolver_service import (
    AssignmentEvidenceContext,
    PlanningEvidenceError,
    PlanningEvidenceResolverService,
)

REMAINING_APPROVAL_EVIDENCE = {
    "status": "pending_fixture",
    "requirement": (
        "A positive grounding case requires a repository fixture whose source/run "
        "identifiers are already bound as Assignment input; no such fixture exists yet."
    ),
}


def _context(*, artifact_hashes: dict[str, str] | None = None) -> AssignmentEvidenceContext:
    return AssignmentEvidenceContext(
        task_id="task-grounding",
        assignment_id="assignment-grounding",
        dispatch_lease_id="lease-grounding",
        tenant_id="tenant-a",
        scope="organization:organization-a",
        source_catalog_id="catalog-grounding",
        source_catalog_hash="a" * 64,
        allowed_source_refs=frozenset(),
        allowed_run_refs=frozenset(),
        artifact_hashes=dict(artifact_hashes or {}),
    )


def _catalog() -> dict:
    return {
        "source_catalog_id": "catalog-grounding",
        "source_catalog_hash": "a" * 64,
        "sources": [],
    }


def _uncertain_category() -> dict:
    return {
        "version": 1,
        "created": "test",
        "updated": "test",
        "project": "organization-grounding",
        "review_basis": {
            "reviewed_commit_range": "not-provided",
            "review_goal": "prove fail-closed grounding",
        },
        "categories": [
            {
                "name": "research",
                "label": "Research",
                "items": [
                    {
                        "id": "ITEM-GROUNDING",
                        "title": "Resolve missing evidence",
                        "status": "open",
                        "priority": "P1",
                        "risk": "high",
                        "type": "research",
                        "depends_on": [],
                        "acceptance_criteria": ["A bound Assignment source fixture exists."],
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
            "source_catalog_id": "catalog-grounding",
            "source_catalog_hash": "a" * 64,
            "allowed_source_refs": [],
            "allowed_run_refs": [],
            "research_summary": "Required Assignment evidence is not available.",
            "claims": [
                {
                    "claim_id": "CLM_0001",
                    "text": "The required evidence has not been supplied.",
                    "claim_type": "uncertain",
                    "citation_refs": [],
                    "confidence": "unverified",
                }
            ],
            "unsupported_notes": ["Positive verification remains pending a bound fixture."],
            "grounding_status": "unverified",
            "grounding_reason": "assignment_evidence_missing",
        },
    }


def test_runtime_binding_rejects_assignment_lease_and_artifact_hash_changes() -> None:
    resolver = PlanningEvidenceResolverService()
    context = _context(artifact_hashes={"artifact:input": "expected-hash"})

    with pytest.raises(PlanningEvidenceError, match="evidence_assignment_mismatch"):
        resolver.validate_runtime_binding(
            expected=context,
            assignment_id="assignment-other",
            dispatch_lease_id=context.dispatch_lease_id,
            artifact_hashes={"artifact:input": "expected-hash"},
        )
    with pytest.raises(PlanningEvidenceError, match="evidence_dispatch_lease_mismatch"):
        resolver.validate_runtime_binding(
            expected=context,
            assignment_id=context.assignment_id,
            dispatch_lease_id="lease-other",
            artifact_hashes={"artifact:input": "expected-hash"},
        )
    with pytest.raises(PlanningEvidenceError, match="evidence_artifact_hash_mismatch"):
        resolver.validate_runtime_binding(
            expected=context,
            assignment_id=context.assignment_id,
            dispatch_lease_id=context.dispatch_lease_id,
            artifact_hashes={"artifact:input": "tampered-hash"},
        )


def test_unbound_or_malformed_reference_is_never_treated_as_grounded() -> None:
    resolver = PlanningEvidenceResolverService()
    result = resolver.verify_grounded_claims(
        answer_payload={
            "schema": "grounded_answer.v1",
            "answer": "This claim has no Assignment-bound source.",
            "claims": [
                {
                    "claim_id": "CLM_0001",
                    "text": "Unsupported claim",
                    "claim_type": "source_fact",
                    "citation_refs": ["unbound-reference"],
                    "confidence": "verified",
                }
            ],
            "unsupported_notes": [],
        },
        source_catalog=_catalog(),
        tool_run_catalog=[],
        context=_context(),
    )

    assert result == {
        "status": "failed",
        "reason": "evidence_reference_id_invalid",
        "verified_claim_count": 0,
        "unverified_claim_count": 0,
        "failed_claims": [],
    }


@pytest.mark.parametrize(
    ("catalog", "reason"),
    [
        (
            {"source_catalog_id": "catalog-other", "source_catalog_hash": "a" * 64, "sources": []},
            "source_catalog_id_mismatch",
        ),
        (
            {"source_catalog_id": "catalog-grounding", "source_catalog_hash": "b" * 64, "sources": []},
            "source_catalog_hash_mismatch",
        ),
    ],
)
def test_foreign_or_stale_catalog_fails_before_claim_verification(
    catalog: dict,
    reason: str,
) -> None:
    result = PlanningEvidenceResolverService().verify_grounded_claims(
        answer_payload={
            "schema": "grounded_answer.v1",
            "answer": "No verified claim is asserted.",
            "claims": [],
            "unsupported_notes": [],
        },
        source_catalog=catalog,
        tool_run_catalog=[],
        context=_context(),
    )

    assert result["status"] == "failed"
    assert result["reason"] == reason
    assert result["verified_claim_count"] == 0


def test_category_without_assignment_evidence_is_recomputed_but_not_promotable() -> None:
    result = PlanningCategoryContractService().validate_and_recompute(
        _uncertain_category(),
        evidence_context=None,
        source_catalog=None,
        tool_run_catalog=[],
    )

    assert result["promotable"] is False
    assert result["grounding"] == {
        "status": "unverified",
        "reason": "category_evidence_context_required",
    }
    assert result["payload"]["meta"]["total_items"] == 1
    assert result["payload"]["meta"]["recommended_order"] == ["ITEM-GROUNDING"]


def test_uncertain_claim_stays_unverified_even_with_matching_empty_catalog() -> None:
    result = PlanningCategoryContractService().validate_and_recompute(
        _uncertain_category(),
        evidence_context=_context(),
        source_catalog=_catalog(),
        tool_run_catalog=[],
    )

    assert result["promotable"] is False
    assert result["grounding"]["status"] == "unverified"
    assert result["grounding"]["reason"] == "category_claims_unverified"
    assert result["grounding"]["verified_claim_count"] == 0
    assert result["grounding"]["unverified_claim_count"] == 1


def test_positive_grounding_fixture_is_explicitly_remaining_release_evidence() -> None:
    assert REMAINING_APPROVAL_EVIDENCE["status"] == "pending_fixture"
    assert "Assignment input" in REMAINING_APPROVAL_EVIDENCE["requirement"]
