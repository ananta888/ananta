"""Deterministic candidate reduction for semantic-compute scheduling."""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ComputeCandidate:
    candidate_id: str
    offered_roles: frozenset[str]
    task_types: frozenset[str]
    self_capacity: int
    measured_capacity: int
    user_limit: int
    reserve_capacity: int
    recent_error_rate: float
    reputation: int
    active_assignments: int
    failure_domain: str
    # Compatibility snapshot only. Productive schedulers inject a current
    # Hub consent authority and pass its decision to ``reduce``.
    consent: bool | None = None
    available: bool = True


@dataclass(frozen=True, slots=True)
class EffectiveCandidate:
    source: ComputeCandidate
    effective_capacity: int
    eligible: bool
    reason_code: str
    score: tuple[int, int, int, str]


class SemanticComputePolicy:
    """Observed facts and user limits always cap peer self-claims."""

    def reduce(
        self,
        candidate: ComputeCandidate,
        *,
        role: str,
        task_type: str,
        minimum_capacity: int,
        consent_authorized: bool | None = None,
    ) -> EffectiveCandidate:
        capacity = min(
            max(0, candidate.self_capacity),
            max(0, candidate.measured_capacity),
            max(0, candidate.user_limit),
        ) - max(0, candidate.reserve_capacity)
        reason = "eligible"
        eligible = True
        if not candidate.available:
            eligible, reason = False, "candidate_unavailable"
        elif not (candidate.consent if consent_authorized is None else consent_authorized):
            eligible, reason = False, "consent_missing"
        role_offers = {role, "executor"} if role == "primary" else {role}
        elif_role_missing = not bool(role_offers & candidate.offered_roles)
        if eligible and (elif_role_missing or task_type not in candidate.task_types):
            eligible, reason = False, "capability_missing"
        elif eligible and (
            not math.isfinite(candidate.recent_error_rate)
            or candidate.recent_error_rate < 0
            or candidate.recent_error_rate > 1
        ):
            eligible, reason = False, "measurement_invalid"
        elif eligible and candidate.recent_error_rate > 0.25:
            eligible, reason = False, "error_budget_exceeded"
        elif eligible and capacity < minimum_capacity:
            eligible, reason = False, "capacity_insufficient"
        score = (
            capacity,
            max(0, min(candidate.reputation, 100)),
            -max(0, candidate.active_assignments),
            candidate.candidate_id,
        )
        return EffectiveCandidate(candidate, capacity, eligible, reason, score)


__all__ = ["ComputeCandidate", "EffectiveCandidate", "SemanticComputePolicy"]
