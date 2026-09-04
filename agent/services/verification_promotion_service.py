"""Headless Hub policy for counterexample regression-test promotion."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from ananta_contracts.verification import counterexample_candidate_digest


@dataclass(frozen=True, slots=True)
class VerificationPromotionDecision:
    allowed: bool
    status: str
    reason_code: str


class VerificationPromotionService:
    def __init__(self, *, auto_approval_enabled: bool) -> None:
        self._auto_approval_enabled = auto_approval_enabled

    def decide(
        self,
        *,
        counterexample: Mapping[str, object],
        test_candidate: Mapping[str, object],
        reproduction_status: str,
        evidence_scope: str,
    ) -> VerificationPromotionDecision:
        if evidence_scope not in {"test", "local", "external", "production"}:
            return VerificationPromotionDecision(False, "blocked", "evidence_scope_invalid")
        if reproduction_status != "counterexample_found":
            return VerificationPromotionDecision(False, "blocked", "concrete_reproduction_required")
        if counterexample_candidate_digest(test_candidate) != counterexample.get("test_candidate_digest"):
            return VerificationPromotionDecision(False, "blocked", "test_candidate_changed")
        if not self._auto_approval_enabled:
            return VerificationPromotionDecision(False, "blocked", "auto_approval_policy_disabled")
        return VerificationPromotionDecision(True, "approved", "auto_approval_policy_granted")


__all__ = ["VerificationPromotionDecision", "VerificationPromotionService"]
