"""Tests for LeaseReevaluationService — extend, switch, propose, no_change."""
from __future__ import annotations

import time

import pytest
from sqlmodel import SQLModel, Session, delete

from agent.database import engine
from agent.db_models import HeuristicDecisionLeaseDB
from agent.repositories.heuristic_lease_repo import HeuristicLeaseRepository
from agent.services.heuristic_runtime.decision_context import DecisionContext
from agent.services.heuristic_runtime.heuristic_registry_service import HeuristicDefinition, HeuristicRegistry
from agent.services.heuristic_runtime.lease_reevaluation_service import (
    LeaseReevaluationService,
    ReevalOutcome,
)


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _fresh_db():
    SQLModel.metadata.create_all(engine)
    yield
    with Session(engine) as s:
        s.exec(delete(HeuristicDecisionLeaseDB))
        s.commit()


def _make_heuristic(hid: str = "snake-follow-default-v1", domain: str = "tui_snake", safety_class: str = "bounded") -> HeuristicDefinition:
    return HeuristicDefinition(
        heuristic_id=hid,
        version="1.0.0",
        domain=domain,
        strategy_kind="follow",
        description="test heuristic",
        deterministic=True,
        safety_class=safety_class,
        capabilities=("motion_suggest",),
        inputs=("context_hash",),
        outputs=("suggested_motion",),
        parameters={},
    )


def _make_registry(*heuristics: HeuristicDefinition) -> HeuristicRegistry:
    reg = HeuristicRegistry(base_path="/nonexistent")
    reg._loaded = True  # skip file loading
    for h in heuristics:
        reg._all.append(h)
        if h.status == "active":
            reg._definitions[h.heuristic_id] = h
    return reg


def _ctx(
    surface: str = "tui_snake",
    ai_status: str = "offline",
    goal: str | None = "g1",
) -> DecisionContext:
    return DecisionContext(
        source_surface=surface,
        ai_status=ai_status,
        active_goal_id=goal,
    )


def _svc(heuristics=None, domain="tui_snake") -> LeaseReevaluationService:
    h = heuristics or [_make_heuristic(domain=domain)]
    repo = HeuristicLeaseRepository()
    reg = _make_registry(*h)
    return LeaseReevaluationService(repo=repo, registry=reg)


# ── no active lease → acquire ─────────────────────────────────────────────────

def test_no_lease_ai_offline_acquires(recwarn):
    svc = _svc()
    ctx = _ctx(ai_status="offline")
    result = svc.evaluate(ctx)
    assert result.outcome == ReevalOutcome.EXTEND  # first acquire maps to EXTEND
    assert result.lease is not None
    assert result.lease.status == "active"


def test_no_lease_no_heuristic_returns_no_heuristic():
    repo = HeuristicLeaseRepository()
    reg = _make_registry()  # empty
    svc = LeaseReevaluationService(repo=repo, registry=reg)
    result = svc.evaluate(_ctx(ai_status="offline"))
    assert result.outcome == ReevalOutcome.NO_HEURISTIC


# ── AI available → propose ────────────────────────────────────────────────────

def test_ai_available_no_lease_proposes():
    svc = _svc()
    result = svc.evaluate(_ctx(ai_status="available"))
    assert result.outcome == ReevalOutcome.PROPOSE_AI


def test_ai_available_with_active_lease_proposes():
    repo = HeuristicLeaseRepository()
    h = _make_heuristic()
    reg = _make_registry(h)
    svc = LeaseReevaluationService(repo=repo, registry=reg)

    ctx = _ctx(ai_status="offline")
    svc.evaluate(ctx)  # acquire lease

    ctx2 = _ctx(ai_status="available")
    result = svc.evaluate(ctx2)
    assert result.outcome == ReevalOutcome.PROPOSE_AI


# ── valid lease, same context → no_change ─────────────────────────────────────

def test_no_change_when_context_unchanged():
    svc = _svc()
    ctx = _ctx(ai_status="offline")
    svc.evaluate(ctx)  # initial acquire
    result = svc.evaluate(ctx)  # same context
    assert result.outcome == ReevalOutcome.NO_CHANGE


# ── context changed → switch or extend ───────────────────────────────────────

def test_extend_when_same_heuristic_after_context_change():
    svc = _svc()
    ctx1 = _ctx(ai_status="offline", goal="g1")
    svc.evaluate(ctx1)

    ctx2 = _ctx(ai_status="offline", goal="g2")  # different hash
    result = svc.evaluate(ctx2)
    # Same heuristic registry → outcome EXTEND
    assert result.outcome == ReevalOutcome.EXTEND
    assert result.previous_lease_id is not None


def test_switch_when_different_heuristic_selected():
    h1 = _make_heuristic("h1", "tui_snake")
    h2 = HeuristicDefinition(
        heuristic_id="h2",
        version="1.0.0",
        domain="tui_snake",
        strategy_kind="lurk",
        description="test lurk",
        deterministic=True,
        safety_class="safety_critical",  # higher rank → will be selected
        capabilities=(),
        inputs=(),
        outputs=(),
        parameters={},
    )
    repo = HeuristicLeaseRepository()
    # First evaluate with only h1 in registry
    reg1 = _make_registry(h1)
    svc1 = LeaseReevaluationService(repo=repo, registry=reg1)
    ctx1 = _ctx(ai_status="offline", goal="g1")
    r1 = svc1.evaluate(ctx1)
    assert r1.lease.heuristic_id == "h1"

    # Second evaluate with h2 as better candidate
    reg2 = _make_registry(h2)
    svc2 = LeaseReevaluationService(repo=repo, registry=reg2)
    ctx2 = _ctx(ai_status="offline", goal="g2")
    r2 = svc2.evaluate(ctx2)
    assert r2.outcome == ReevalOutcome.SWITCH
    assert r2.lease.heuristic_id == "h2"


# ── lease TTL expired → reacquire ─────────────────────────────────────────────

def test_reacquires_after_ttl_expiry():
    repo = HeuristicLeaseRepository()
    h = _make_heuristic()
    reg = _make_registry(h)
    svc = LeaseReevaluationService(repo=repo, registry=reg)

    ctx = _ctx(ai_status="offline")
    first = svc.evaluate(ctx)
    assert first.lease is not None

    # Manually expire the lease
    lease = repo.get_by_id(first.lease.id)
    lease.deadline_at = time.time() - 1
    repo.save(lease)

    second = svc.evaluate(ctx)
    assert second.outcome in {ReevalOutcome.EXTEND, ReevalOutcome.SWITCH}
    assert second.lease is not None
    assert second.lease.id != first.lease.id


# ── handle_expiry ─────────────────────────────────────────────────────────────

def test_handle_expiry_marks_overdue_leases():
    repo = HeuristicLeaseRepository()
    h = _make_heuristic()
    reg = _make_registry(h)
    svc = LeaseReevaluationService(repo=repo, registry=reg)

    ctx = _ctx(ai_status="offline")
    result = svc.evaluate(ctx)
    lease = result.lease
    lease.deadline_at = time.time() - 1
    repo.save(lease)

    count = svc.handle_expiry()
    assert count == 1

    refreshed = repo.get_by_id(lease.id)
    assert refreshed.status == "expired"


# ── reason codes ─────────────────────────────────────────────────────────────

def test_reason_codes_include_ai_status():
    svc = _svc()
    ctx = _ctx(ai_status="timeout")
    result = svc.evaluate(ctx)
    assert result.lease is not None
    assert "ai_timeout" in result.lease.reason_codes


def test_initial_reason_code():
    svc = _svc()
    ctx = _ctx(ai_status="offline")
    result = svc.evaluate(ctx)
    assert "initial_acquire" in result.lease.reason_codes
