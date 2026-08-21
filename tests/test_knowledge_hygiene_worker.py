from __future__ import annotations

import pytest

from ananta_contracts.knowledge_hygiene import (
    KnowledgeHygieneRun,
    SourceRevisionBinding,
)
from worker.knowledge_hygiene import (
    ClaimExtractionHandler,
    KnowledgeHygieneAssignment,
    KnowledgeHygieneWorkerError,
)


def _assignment(*, expires_at: float = 100.0) -> KnowledgeHygieneAssignment:
    binding = SourceRevisionBinding(
        source_id="SRC_0001",
        source_revision="rev-1",
        content_sha256="a" * 64,
        allowed_locators=("docs/source.md#setting",),
    )
    budgets = {"claims": 2, "candidate_pairs": 10, "pages": 1}
    digest = KnowledgeHygieneRun.calculate_assignment_digest(
        run_id="RUN_0001",
        project_id="project-a",
        source_bindings=(binding,),
        policy_version="knowledge_claim_precedence.v1",
        profile_name="fixture-v1",
        budgets=budgets,
    )
    return KnowledgeHygieneAssignment(
        run_id="RUN_0001",
        project_id="project-a",
        assignment_digest=digest,
        source_bindings=(binding,),
        allowed_operations=("extract_claims",),
        profile_name="fixture-v1",
        policy_version="knowledge_claim_precedence.v1",
        budgets=budgets,
        expires_at=expires_at,
    )


def test_fixture_extraction_is_bound_and_never_invents_source_ids() -> None:
    handler = ClaimExtractionHandler()
    result = handler.handle(
        assignment=_assignment(),
        records=(
            {
                "source_id": "SRC_0001",
                "source_revision": "rev-1",
                "source_locator": "docs/source.md#setting",
                "content_sha256": "a" * 64,
                "claims": [{"subject": "service", "predicate": "enabled", "value": True}],
            },
        ),
        now=10.0,
        fixture_mode=True,
    )

    assert result.claims[0]["source_id"] == "SRC_0001"
    assert result.claims[0]["source_revision"] == "rev-1"
    assert not hasattr(handler, "dispatch")
    assert not hasattr(handler, "enqueue")


def test_expired_assignment_and_locator_escape_fail_closed() -> None:
    handler = ClaimExtractionHandler()
    with pytest.raises(KnowledgeHygieneWorkerError, match="assignment_expired"):
        handler.handle(assignment=_assignment(expires_at=1.0), records=(), now=2.0, fixture_mode=True)

    with pytest.raises(KnowledgeHygieneWorkerError, match="locator_outside_assignment"):
        handler.handle(
            assignment=_assignment(),
            records=(
                {
                    "source_id": "SRC_0001",
                    "source_revision": "rev-1",
                    "source_locator": "docs/other.md",
                    "content_sha256": "a" * 64,
                    "claims": [],
                },
            ),
            now=10.0,
            fixture_mode=True,
        )


def test_assignment_digest_tampering_is_rejected() -> None:
    valid = _assignment()
    with pytest.raises(KnowledgeHygieneWorkerError, match="assignment_digest_mismatch"):
        KnowledgeHygieneAssignment(
            run_id=valid.run_id,
            project_id=valid.project_id,
            assignment_digest="b" * 64,
            source_bindings=valid.source_bindings,
            allowed_operations=valid.allowed_operations,
            profile_name=valid.profile_name,
            policy_version=valid.policy_version,
            budgets=valid.budgets,
            expires_at=valid.expires_at,
        )
