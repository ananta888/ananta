"""LeaseReevaluationService — evaluates whether an active lease should extend, switch, or propose.

Reevaluation triggers:
  1. TTL expiry detected via mark_expired_batch()
  2. Context hash changed mid-lease (significant state change)
  3. AI becomes available / unavailable (ai_status transition)

Outcomes:
  extend     — same heuristic, same context; reacquire with fresh TTL
  switch     — different heuristic better matches new context; acquire new lease
  propose    — context suggests AI handoff; no new heuristic lease acquired
  no_change  — lease still valid, nothing to do
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum
from typing import Any

from agent.db_models import HeuristicDecisionLeaseDB
from agent.repositories.heuristic_lease_repo import HeuristicLeaseRepository
from agent.services.heuristic_runtime.decision_context import DecisionContext
from agent.services.heuristic_runtime.heuristic_registry_service import HeuristicRegistry, get_heuristic_registry


class ReevalOutcome(str, Enum):
    NO_CHANGE = "no_change"
    EXTEND = "extend"
    SWITCH = "switch"
    PROPOSE_AI = "propose_ai"
    NO_HEURISTIC = "no_heuristic"


@dataclass
class ReevalResult:
    outcome: ReevalOutcome
    lease: HeuristicDecisionLeaseDB | None = None
    previous_lease_id: str | None = None
    reason: str = ""
    context_hash: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome.value,
            "lease_id": self.lease.id if self.lease else None,
            "previous_lease_id": self.previous_lease_id,
            "reason": self.reason,
            "context_hash": self.context_hash,
        }


class LeaseReevaluationService:
    def __init__(
        self,
        repo: HeuristicLeaseRepository | None = None,
        registry: HeuristicRegistry | None = None,
    ) -> None:
        self._repo = repo or HeuristicLeaseRepository()
        self._registry = registry or get_heuristic_registry()

    def evaluate(self, ctx: DecisionContext, *, now_ts: float | None = None) -> ReevalResult:
        """Main entry point. Evaluates whether to extend, switch, or propose for the given context."""
        now = float(now_ts or time.time())
        domain = ctx.source_surface
        current_hash = ctx.context_hash

        # If AI is available, yield control — no new heuristic lease needed
        if ctx.ai_status == "available":
            return ReevalResult(
                outcome=ReevalOutcome.PROPOSE_AI,
                reason="ai_available",
                context_hash=current_hash,
            )

        existing = self._repo.get_active(domain, now_ts=now)

        # No active lease — try to acquire one
        if existing is None:
            return self._acquire_best(ctx, domain, current_hash, now, previous_lease_id=None)

        # Lease still valid — check if context changed
        if existing.context_hash == current_hash:
            remaining = existing.deadline_at - now
            if remaining > 0:
                return ReevalResult(
                    outcome=ReevalOutcome.NO_CHANGE,
                    lease=existing,
                    reason="context_unchanged_lease_valid",
                    context_hash=current_hash,
                )

        # Context changed or TTL expired — reevaluate
        return self._acquire_best(ctx, domain, current_hash, now, previous_lease_id=existing.id)

    def handle_expiry(self, *, now_ts: float | None = None) -> int:
        """Sweep expired leases. Returns count marked."""
        return self._repo.mark_expired_batch(now_ts=now_ts)

    # ── internal ─────────────────────────────────────────────────────────────

    def _acquire_best(
        self,
        ctx: DecisionContext,
        domain: str,
        context_hash: str,
        now: float,
        previous_lease_id: str | None,
    ) -> ReevalResult:
        candidates = self._registry.get_active(domain)
        if not candidates:
            return ReevalResult(
                outcome=ReevalOutcome.NO_HEURISTIC,
                previous_lease_id=previous_lease_id,
                reason="no_active_heuristic_for_domain",
                context_hash=context_hash,
            )

        # Select best candidate: prefer deterministic, highest-safety
        best = _select_best(candidates, ctx)

        reason_codes = ["context_changed"] if previous_lease_id else ["initial_acquire"]
        if ctx.ai_status != "available":
            reason_codes.append(f"ai_{ctx.ai_status}")

        lease = self._repo.acquire(
            heuristic_id=best.heuristic_id,
            version=best.version,
            domain=domain,
            context_hash=context_hash,
            selected_by="heuristic_self",
            reason_codes=reason_codes,
        )

        outcome = ReevalOutcome.SWITCH if previous_lease_id else ReevalOutcome.EXTEND
        # True "extend" = same heuristic reacquired
        if previous_lease_id:
            prev = self._repo.get_by_id(previous_lease_id)
            if prev and prev.heuristic_id == best.heuristic_id:
                outcome = ReevalOutcome.EXTEND

        return ReevalResult(
            outcome=outcome,
            lease=lease,
            previous_lease_id=previous_lease_id,
            reason="lease_acquired",
            context_hash=context_hash,
        )


_SAFETY_ORDER = {"safety_critical": 3, "bounded": 2, "low_risk": 1}


def _select_best(candidates: list, ctx: DecisionContext) -> Any:
    """Prefer deterministic heuristics; break ties by safety_class rank."""
    def score(h: Any) -> tuple[int, int]:
        det = 1 if getattr(h, "deterministic", False) else 0
        safety = _SAFETY_ORDER.get(getattr(h, "safety_class", "low_risk"), 0)
        return (det, safety)

    return max(candidates, key=score)
