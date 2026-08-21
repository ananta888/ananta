"""Generic claim comparison policy; precedence never decides truth."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping

from ananta_contracts.knowledge_hygiene import CoverageState, KnowledgeClaim


KNOWLEDGE_CLAIM_PRECEDENCE_VERSION = "knowledge_claim_precedence.v1"


@dataclass(frozen=True, slots=True)
class ClaimComparison:
    relation: str
    candidate_conflict: bool
    severity: str
    reason_codes: tuple[str, ...]
    preferred_review_order: tuple[str, str]


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def temporal_overlap(left: KnowledgeClaim, right: KnowledgeClaim) -> bool:
    left_start = _parse_time(left.effective_from)
    left_end = _parse_time(left.effective_to)
    right_start = _parse_time(right.effective_from)
    right_end = _parse_time(right.effective_to)
    if left_end is not None and right_start is not None and left_end < right_start:
        return False
    if right_end is not None and left_start is not None and right_end < left_start:
        return False
    return True


def _review_score(claim: KnowledgeClaim, trust: Mapping[str, int]) -> tuple[int, str, str]:
    freshness = claim.effective_from or claim.source_revision
    return (int(trust.get(claim.source_id, 0)), freshness, claim.claim_id)


def compare_claims(
    left: KnowledgeClaim,
    right: KnowledgeClaim,
    *,
    source_trust: Mapping[str, int] | None = None,
) -> ClaimComparison:
    """Classify a pair without asserting which side is true.

    Trust and freshness only influence review order/severity.  They can never
    suppress a grounded side or convert a candidate into an automatic decision.
    """

    trust = source_trust or {}
    ordered = tuple(
        claim.claim_id
        for claim in sorted((left, right), key=lambda item: _review_score(item, trust), reverse=True)
    )
    if left.project_id != right.project_id or left.scope != right.scope:
        return ClaimComparison("out_of_scope", False, "none", ("scope_mismatch",), ordered)
    if left.subject.casefold().strip() != right.subject.casefold().strip():
        return ClaimComparison("unrelated", False, "none", ("subject_mismatch",), ordered)
    if left.predicate.casefold().strip() != right.predicate.casefold().strip():
        return ClaimComparison("unrelated", False, "none", ("predicate_mismatch",), ordered)
    if (right.claim_id, right.revision) in left.supersedes_claim_refs:
        return ClaimComparison("superseded", False, "none", ("left_explicitly_supersedes_right",), ordered)
    if (left.claim_id, left.revision) in right.supersedes_claim_refs:
        return ClaimComparison("superseded", False, "none", ("right_explicitly_supersedes_left",), ordered)
    if {left.assertion_kind, right.assertion_kind} == {"target", "actual"}:
        return ClaimComparison("compatible_parallel", False, "none", ("target_actual_pair",), ordered)
    if not temporal_overlap(left, right):
        return ClaimComparison("temporally_distinct", False, "none", ("non_overlapping_periods",), ordered)
    if left.normalized_payload == right.normalized_payload:
        return ClaimComparison("confirmation", False, "none", ("same_normalized_claim",), ordered)
    if left.coverage is not CoverageState.COMPLETE or right.coverage is not CoverageState.COMPLETE:
        return ClaimComparison("candidate", True, "unknown", ("incomplete_coverage",), ordered)
    trust_gap = abs(int(trust.get(left.source_id, 0)) - int(trust.get(right.source_id, 0)))
    severity = "high" if trust_gap <= 1 else "medium"
    return ClaimComparison("candidate", True, severity, ("overlapping_claim_difference",), ordered)
