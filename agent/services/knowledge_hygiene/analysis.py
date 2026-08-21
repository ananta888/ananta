"""Pure, bounded claim analysis policies for Knowledge Hygiene."""

from __future__ import annotations

import math
import re
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from itertools import combinations
from typing import Mapping, Protocol, Sequence

from ananta_contracts.knowledge_claim_precedence import compare_claims
from ananta_contracts.knowledge_hygiene import (
    ConflictState,
    CoverageState,
    KnowledgeClaim,
    KnowledgeConflict,
    canonical_digest,
)


ANALYSIS_VERSION = "knowledge_hygiene_analysis.v1"
_SPACE = re.compile(r"\s+")
_NEGATED = frozenset({"false", "no", "not", "never", "disabled", "inactive", "absent"})
_AFFIRMED = frozenset({"true", "yes", "always", "enabled", "active", "present"})
_STATUS_CONFLICTS = frozenset(
    {
        frozenset({"active", "inactive"}),
        frozenset({"open", "closed"}),
        frozenset({"approved", "rejected"}),
        frozenset({"available", "unavailable"}),
        frozenset({"supported", "unsupported"}),
    }
)


class SemanticSimilarityPort(Protocol):
    """Existing embedding infrastructure can be adapted behind this narrow port."""

    profile_name: str

    def similarity(self, left: str, right: str) -> float: ...


@dataclass(frozen=True, slots=True)
class DuplicateCandidate:
    left_claim_id: str
    right_claim_id: str
    method: str
    score: float
    profile_name: str | None
    evidence: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AnalysisResult:
    conflicts: tuple[KnowledgeConflict, ...]
    exact_duplicates: tuple[DuplicateCandidate, ...]
    semantic_duplicates: tuple[DuplicateCandidate, ...]
    coverage: CoverageState
    evaluated_pairs: int
    skipped_pairs: int
    reason_codes: tuple[str, ...]


def normalize_text(value: object) -> str:
    return _SPACE.sub(" ", str(value).strip().casefold())


def _number(value: object) -> Decimal | None:
    if isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value).replace(",", "."))
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() else None


def numeric_difference(left: KnowledgeClaim, right: KnowledgeClaim) -> tuple[bool, tuple[str, ...]]:
    left_number = _number(left.value)
    right_number = _number(right.value)
    if left_number is None or right_number is None:
        return False, ()
    if normalize_text(left.unit or "") != normalize_text(right.unit or ""):
        return False, ("incompatible_units",)
    if left_number == right_number:
        return False, ("equal_numeric_value",)
    return True, (f"numeric_values_differ:{left_number}:{right_number}",)


def negation_difference(left: KnowledgeClaim, right: KnowledgeClaim) -> tuple[bool, tuple[str, ...]]:
    left_value = normalize_text(left.value)
    right_value = normalize_text(right.value)
    if {left_value, right_value} & _NEGATED and {left_value, right_value} & _AFFIRMED:
        return True, ("affirmation_negation_pair",)
    return False, ()


def status_difference(left: KnowledgeClaim, right: KnowledgeClaim) -> tuple[bool, tuple[str, ...]]:
    pair = frozenset({normalize_text(left.status or left.value), normalize_text(right.status or right.value)})
    if pair in _STATUS_CONFLICTS:
        return True, ("incompatible_status_pair",)
    return False, ()


def exact_duplicate_candidates(claims: Sequence[KnowledgeClaim]) -> tuple[DuplicateCandidate, ...]:
    groups: dict[str, list[KnowledgeClaim]] = defaultdict(list)
    for claim in claims:
        groups[canonical_digest(claim.normalized_payload)].append(claim)
    results: list[DuplicateCandidate] = []
    for group in groups.values():
        if len(group) < 2:
            continue
        for left, right in combinations(sorted(group, key=lambda item: (item.claim_id, item.revision)), 2):
            if left.project_id != right.project_id:
                continue
            results.append(
                DuplicateCandidate(
                    left_claim_id=left.claim_id,
                    right_claim_id=right.claim_id,
                    method="exact_normalized_payload",
                    score=1.0,
                    profile_name=None,
                    evidence=("order_independent_exact_match",),
                )
            )
    return tuple(results)


def semantic_duplicate_candidates(
    claims: Sequence[KnowledgeClaim],
    *,
    similarity: SemanticSimilarityPort,
    threshold: float,
    max_pairs: int,
) -> tuple[DuplicateCandidate, ...]:
    results: list[DuplicateCandidate] = []
    evaluated = 0
    buckets = _buckets(claims)
    for bucket in sorted(buckets):
        for left, right in combinations(buckets[bucket], 2):
            if evaluated >= max_pairs:
                return tuple(results)
            evaluated += 1
            score = float(similarity.similarity(str(left.value), str(right.value)))
            if math.isfinite(score) and score >= threshold and left.normalized_payload != right.normalized_payload:
                results.append(
                    DuplicateCandidate(
                        left_claim_id=left.claim_id,
                        right_claim_id=right.claim_id,
                        method="semantic_candidate",
                        score=score,
                        profile_name=similarity.profile_name,
                        evidence=(f"threshold:{threshold:.4f}", f"score:{score:.4f}"),
                    )
                )
    return tuple(results)


def _buckets(claims: Sequence[KnowledgeClaim]) -> dict[tuple[str, str, str, str], list[KnowledgeClaim]]:
    buckets: dict[tuple[str, str, str, str], list[KnowledgeClaim]] = defaultdict(list)
    for claim in claims:
        buckets[(claim.project_id, claim.scope, normalize_text(claim.subject), normalize_text(claim.predicate))].append(claim)
    for values in buckets.values():
        values.sort(key=lambda item: (item.claim_id, item.revision))
    return buckets


def _conflict_for_pair(
    left: KnowledgeClaim,
    right: KnowledgeClaim,
    *,
    conflict_type: str,
    severity: str,
    evidence: tuple[str, ...],
    coverage: CoverageState,
    now: float,
) -> KnowledgeConflict:
    ordered = sorted((left, right), key=lambda item: (item.claim_id, item.revision))
    pair_digest = canonical_digest(
        {
            "version": ANALYSIS_VERSION,
            "project_id": left.project_id,
            "left": [ordered[0].claim_id, ordered[0].revision, ordered[0].record_digest],
            "right": [ordered[1].claim_id, ordered[1].revision, ordered[1].record_digest],
            "type": conflict_type,
        }
    )
    return KnowledgeConflict(
        conflict_id=f"KHC_{pair_digest[:24]}",
        project_id=left.project_id,
        left_claim_id=ordered[0].claim_id,
        left_claim_revision=ordered[0].revision,
        left_claim_digest=ordered[0].record_digest,
        right_claim_id=ordered[1].claim_id,
        right_claim_revision=ordered[1].revision,
        right_claim_digest=ordered[1].record_digest,
        conflict_type=conflict_type,
        severity=severity,
        evidence=evidence,
        coverage=coverage,
        state=ConflictState.OPEN,
        created_at=now,
        updated_at=now,
    )


def _pair_coverage(left: KnowledgeClaim, right: KnowledgeClaim) -> CoverageState:
    if CoverageState.UNKNOWN in {left.coverage, right.coverage}:
        return CoverageState.UNKNOWN
    if CoverageState.PARTIAL in {left.coverage, right.coverage}:
        return CoverageState.PARTIAL
    return CoverageState.COMPLETE


def analyze_claims(
    claims: Sequence[KnowledgeClaim],
    *,
    max_candidate_pairs: int,
    now: float,
    source_trust: Mapping[str, int] | None = None,
    semantic_similarity: SemanticSimilarityPort | None = None,
    semantic_threshold: float = 0.92,
) -> AnalysisResult:
    if max_candidate_pairs <= 0:
        raise ValueError("max_candidate_pairs_must_be_positive")
    duplicates = exact_duplicate_candidates(claims)
    conflicts: list[KnowledgeConflict] = []
    evaluated = 0
    skipped = 0
    exhausted = False
    buckets = _buckets(claims)
    for bucket in sorted(buckets):
        bucket_claims = buckets[bucket]
        bucket_pairs = len(bucket_claims) * (len(bucket_claims) - 1) // 2
        evaluated_in_bucket = 0
        if evaluated >= max_candidate_pairs:
            skipped += bucket_pairs
            exhausted = exhausted or bucket_pairs > 0
            continue
        for left, right in combinations(bucket_claims, 2):
            if evaluated >= max_candidate_pairs:
                skipped += bucket_pairs - evaluated_in_bucket
                exhausted = True
                break
            evaluated += 1
            evaluated_in_bucket += 1
            precedence = compare_claims(left, right, source_trust=source_trust)
            if not precedence.candidate_conflict:
                continue
            matched = False
            for conflict_type, validator in (
                ("numeric", numeric_difference),
                ("negation", negation_difference),
                ("status", status_difference),
            ):
                differs, evidence = validator(left, right)
                if differs:
                    conflicts.append(
                        _conflict_for_pair(
                            left,
                            right,
                            conflict_type=conflict_type,
                            severity=precedence.severity,
                            evidence=precedence.reason_codes + evidence,
                            coverage=_pair_coverage(left, right),
                            now=now,
                        )
                    )
                    matched = True
                    break
            if not matched and normalize_text(left.value) != normalize_text(right.value):
                conflicts.append(
                    _conflict_for_pair(
                        left,
                        right,
                        conflict_type="deterministic_value",
                        severity=precedence.severity,
                        evidence=precedence.reason_codes + ("normalized_values_differ",),
                        coverage=_pair_coverage(left, right),
                        now=now,
                    )
                )
    semantic = (
        semantic_duplicate_candidates(
            claims,
            similarity=semantic_similarity,
            threshold=semantic_threshold,
            max_pairs=max_candidate_pairs,
        )
        if semantic_similarity is not None
        else ()
    )
    if any(claim.coverage is CoverageState.UNKNOWN for claim in claims):
        coverage = CoverageState.UNKNOWN
    elif exhausted or any(claim.coverage is CoverageState.PARTIAL for claim in claims):
        coverage = CoverageState.PARTIAL
    else:
        coverage = CoverageState.COMPLETE
    if exhausted:
        reasons = ("candidate_budget_exhausted",)
    elif coverage is CoverageState.UNKNOWN:
        reasons = ("input_coverage_unknown",)
    elif coverage is CoverageState.PARTIAL:
        reasons = ("input_coverage_partial",)
    else:
        reasons = ("analysis_complete",)
    return AnalysisResult(
        conflicts=tuple(conflicts),
        exact_duplicates=duplicates,
        semantic_duplicates=semantic,
        coverage=coverage,
        evaluated_pairs=evaluated,
        skipped_pairs=skipped,
        reason_codes=reasons,
    )
