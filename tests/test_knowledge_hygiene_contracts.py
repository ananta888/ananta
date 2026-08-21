from __future__ import annotations

from dataclasses import replace

import pytest

from agent.services.knowledge_hygiene.analysis import analyze_claims, exact_duplicate_candidates
from ananta_contracts.knowledge_claim_precedence import compare_claims
from ananta_contracts.knowledge_hygiene import (
    CoverageState,
    KnowledgeClaim,
    KnowledgeHygieneContractError,
)


def _claim(
    claim_id: str,
    *,
    source_id: str,
    value: object,
    assertion_kind: str = "actual",
    effective_from: str | None = None,
    effective_to: str | None = None,
    coverage: CoverageState = CoverageState.COMPLETE,
) -> KnowledgeClaim:
    return KnowledgeClaim(
        claim_id=claim_id,
        project_id="project-a",
        revision=1,
        subject="API",
        predicate="timeout",
        value=value,
        unit="seconds",
        source_id=source_id,
        source_revision="rev-1",
        source_locator=f"docs/{claim_id}.md#timeout",
        source_content_sha256="a" * 64,
        extraction_run_id="RUN_0001",
        assertion_kind=assertion_kind,
        effective_from=effective_from,
        effective_to=effective_to,
        coverage=coverage,
        created_at=10.0,
    )


def test_claim_rejects_unknown_or_invented_source_identifier() -> None:
    with pytest.raises(KnowledgeHygieneContractError, match="unverified_source_id"):
        _claim("left", source_id="source-from-path", value=10)


def test_target_and_actual_are_not_a_contradiction() -> None:
    target = _claim("target", source_id="SRC_0001", value=10, assertion_kind="target")
    actual = _claim("actual", source_id="SRC_0002", value=12, assertion_kind="actual")

    result = compare_claims(target, actual)

    assert result.relation == "compatible_parallel"
    assert result.candidate_conflict is False
    assert result.reason_codes == ("target_actual_pair",)


def test_non_overlapping_periods_are_not_a_contradiction() -> None:
    old = _claim(
        "old",
        source_id="SRC_0001",
        value=10,
        effective_from="2025-01-01T00:00:00+00:00",
        effective_to="2025-12-31T00:00:00+00:00",
    )
    new = _claim(
        "new",
        source_id="SRC_0002",
        value=12,
        effective_from="2026-01-01T00:00:00+00:00",
    )

    result = compare_claims(old, new)

    assert result.relation == "temporally_distinct"
    assert result.candidate_conflict is False


def test_explicit_supersession_is_visible_but_not_a_contradiction() -> None:
    old = _claim("old", source_id="SRC_0001", value=10)
    current = replace(
        _claim("current", source_id="SRC_0002", value=12),
        supersedes_claim_refs=((old.claim_id, old.revision),),
    )

    result = compare_claims(old, current)

    assert result.relation == "superseded"
    assert result.candidate_conflict is False


def test_exact_duplicates_are_order_independent_and_non_destructive() -> None:
    left = _claim("a", source_id="SRC_0001", value=10)
    right = replace(
        left,
        claim_id="b",
        source_id="SRC_0002",
        source_locator="docs/b.md#timeout",
    )

    forward = exact_duplicate_candidates((left, right))
    reverse = exact_duplicate_candidates((right, left))

    assert forward == reverse
    assert forward[0].method == "exact_normalized_payload"
    assert {forward[0].left_claim_id, forward[0].right_claim_id} == {"a", "b"}


def test_partial_pair_budget_never_reports_complete_or_false_zero() -> None:
    claims = tuple(
        _claim(f"claim-{index}", source_id="SRC_0001", value=index)
        for index in range(4)
    )

    result = analyze_claims(claims, max_candidate_pairs=1, now=20.0)

    assert result.coverage is CoverageState.PARTIAL
    assert result.evaluated_pairs == 1
    assert result.skipped_pairs == 5
    assert "candidate_budget_exhausted" in result.reason_codes


def test_incomplete_claim_inputs_propagate_unknown_coverage() -> None:
    left = _claim("left", source_id="SRC_0001", value="enabled")
    right = _claim(
        "right",
        source_id="SRC_0002",
        value="disabled",
        coverage=CoverageState.UNKNOWN,
    )

    result = analyze_claims((left, right), max_candidate_pairs=10, now=20.0)

    assert result.coverage is CoverageState.UNKNOWN
    assert result.conflicts[0].coverage is CoverageState.UNKNOWN
    assert result.conflicts[0].severity == "unknown"


def test_three_real_conflict_classes_and_harmless_confirmation() -> None:
    numeric_left = replace(_claim("n-left", source_id="SRC_0001", value=2), subject="numeric")
    numeric_right = replace(_claim("n-right", source_id="SRC_0002", value=3), subject="numeric")
    negation_left = replace(_claim("b-left", source_id="SRC_0001", value=True), subject="boolean", unit=None)
    negation_right = replace(_claim("b-right", source_id="SRC_0002", value=False), subject="boolean", unit=None)
    status_left = replace(
        _claim("s-left", source_id="SRC_0001", value="service"),
        subject="status",
        status="active",
        unit=None,
    )
    status_right = replace(
        _claim("s-right", source_id="SRC_0002", value="service"),
        subject="status",
        status="inactive",
        unit=None,
    )
    confirmation_left = replace(_claim("c-left", source_id="SRC_0001", value=5), subject="confirmation")
    confirmation_right = replace(
        _claim("c-right", source_id="SRC_0002", value=5),
        subject="confirmation",
    )

    result = analyze_claims(
        (
            numeric_left,
            numeric_right,
            negation_left,
            negation_right,
            status_left,
            status_right,
            confirmation_left,
            confirmation_right,
        ),
        max_candidate_pairs=20,
        now=20.0,
    )

    assert {item.conflict_type for item in result.conflicts} == {"numeric", "negation", "status"}
    assert len(result.exact_duplicates) == 1
