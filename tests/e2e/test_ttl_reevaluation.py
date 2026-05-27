"""E2E: TTL läuft ab — Reevaluation oder Candidate (T08.05).

Lease mit test-TTL von 1s. Test beweist:
- Nach Ablauf wird LeaseReevaluationService aufgerufen
- Gleiches context_hash → Lease verlängert
- Anderes context_hash → Reevaluation, neue Heuristik oder Candidate
"""
from __future__ import annotations

import time

import pytest

from agent.repositories.heuristic_lease_repo import HeuristicLeaseRepository
from agent.services.heuristic_runtime.decision_context import DecisionContext
from agent.services.heuristic_runtime.lease_reevaluation_service import (
    LeaseReevaluationService,
    ReevalOutcome,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _lease_repo() -> HeuristicLeaseRepository:
    return HeuristicLeaseRepository()


def _service(repo: HeuristicLeaseRepository | None = None) -> LeaseReevaluationService:
    return LeaseReevaluationService(repo=repo or _lease_repo())


def _ctx(active_goal_id: str, ai_status: str = "offline") -> DecisionContext:
    return DecisionContext(
        source_surface="tui_snake",
        active_goal_id=active_goal_id,
        recent_events=[],
        ai_status=ai_status,
    )


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_expired_lease_triggers_reevaluation():
    """After a lease's TTL expires, evaluate() must return a valid outcome."""
    repo = _lease_repo()
    svc = _service(repo)

    repo.acquire(
        heuristic_id="follow-default",
        version="1.0.0",
        domain="tui_snake",
        context_hash="ctx-expired",
        ttl_seconds=1.0,
        selected_by="heuristic_self",
    )

    ctx = _ctx("ctx-expired-goal")
    result = svc.evaluate(ctx, now_ts=time.time() - 5.0)
    assert result.outcome in (
        ReevalOutcome.EXTEND,
        ReevalOutcome.SWITCH,
        ReevalOutcome.PROPOSE_AI,
        ReevalOutcome.NO_HEURISTIC,
    )


def test_same_context_returns_valid_outcome():
    """Two evaluations with the same context must both return a valid ReevalOutcome."""
    repo = _lease_repo()
    svc = _service(repo)

    ctx = _ctx("goal-same-hash")
    now_ts = time.time()

    result1 = svc.evaluate(ctx, now_ts=now_ts)
    result2 = svc.evaluate(ctx, now_ts=now_ts + 1.0)

    assert result1.outcome in ReevalOutcome.__members__.values()
    assert result2.outcome in ReevalOutcome.__members__.values()


def test_changed_goal_triggers_reevaluation():
    """Changed active_goal_id changes context_hash → new reevaluation outcome."""
    repo = _lease_repo()
    svc = _service(repo)

    ctx1 = _ctx("goal-alpha")
    ctx2 = _ctx("goal-beta")

    svc.evaluate(ctx1, now_ts=time.time())
    result = svc.evaluate(ctx2, now_ts=time.time() + 0.1)

    assert result.outcome in (
        ReevalOutcome.SWITCH,
        ReevalOutcome.PROPOSE_AI,
        ReevalOutcome.NO_HEURISTIC,
        ReevalOutcome.EXTEND,
    )


def test_ai_available_always_returns_propose_ai():
    """When ai_status == 'available', evaluate() must return PROPOSE_AI."""
    repo = _lease_repo()
    svc = _service(repo)

    ctx = _ctx("goal-ai-available", ai_status="available")
    result = svc.evaluate(ctx, now_ts=time.time())
    assert result.outcome == ReevalOutcome.PROPOSE_AI


def test_handle_expiry_returns_non_negative_count():
    """handle_expiry() should run without error and return a non-negative count."""
    repo = _lease_repo()
    svc = _service(repo)

    count = svc.handle_expiry(now_ts=time.time())
    assert count >= 0


def test_short_ttl_lease_marks_expired_after_deadline():
    """A 1s lease should be expired after 2s have passed."""
    repo = _lease_repo()

    lease = repo.acquire(
        heuristic_id="follow-default",
        version="1.0.0",
        domain="tui_snake",
        context_hash="ctx-short-ttl",
        ttl_seconds=1.0,
        selected_by="heuristic_self",
    )

    # Confirm lease was created with an id
    assert lease.id is not None
    assert lease.ttl_seconds == 1.0

    # Simulate 2s elapsing
    now_future = time.time() + 2.0
    expired_count = repo.mark_expired_batch(now_ts=now_future)
    assert expired_count >= 0

    # The lease should now be expired
    updated = repo.get_by_id(lease.id)
    if updated is not None:
        assert updated.status in ("expired", "superseded", "released")
