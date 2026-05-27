"""Tests for HeuristicLeaseRepository — TTL, expiry, release, supersede semantics."""
from __future__ import annotations

import time

import pytest
from sqlmodel import SQLModel

from agent.database import engine
from agent.repositories.heuristic_lease_repo import HeuristicLeaseRepository, _DOMAIN_TTL_DEFAULTS


@pytest.fixture(autouse=True)
def _fresh_db():
    SQLModel.metadata.create_all(engine)
    yield
    from agent.db_models import HeuristicDecisionLeaseDB
    from sqlmodel import Session, delete
    with Session(engine) as s:
        s.exec(delete(HeuristicDecisionLeaseDB))
        s.commit()


@pytest.fixture()
def repo() -> HeuristicLeaseRepository:
    return HeuristicLeaseRepository()


# ── acquire ──────────────────────────────────────────────────────────────────

def test_acquire_creates_active_lease(repo):
    lease = repo.acquire(
        heuristic_id="snake-follow-default-v1",
        version="1.0.0",
        domain="tui_snake",
        context_hash="abc123",
    )
    assert lease.status == "active"
    assert lease.heuristic_id == "snake-follow-default-v1"
    assert lease.domain == "tui_snake"
    assert lease.deadline_at > lease.acquired_at


def test_acquire_uses_domain_ttl_default(repo):
    lease = repo.acquire(
        heuristic_id="h1", version="1.0.0", domain="chat_codecompass", context_hash="x"
    )
    expected_ttl = _DOMAIN_TTL_DEFAULTS["chat_codecompass"]
    assert abs(lease.ttl_seconds - expected_ttl) < 0.1
    assert abs((lease.deadline_at - lease.acquired_at) - expected_ttl) < 0.5


def test_acquire_accepts_custom_ttl(repo):
    lease = repo.acquire(
        heuristic_id="h1", version="1.0.0", domain="tui_snake", context_hash="x",
        ttl_seconds=3.0,
    )
    assert lease.ttl_seconds == 3.0


def test_acquire_supersedes_previous_active_lease(repo):
    first = repo.acquire(heuristic_id="h1", version="1.0.0", domain="tui_snake", context_hash="a")
    second = repo.acquire(heuristic_id="h2", version="1.0.0", domain="tui_snake", context_hash="b")

    refreshed_first = repo.get_by_id(first.id)
    assert refreshed_first.status == "superseded"
    assert second.status == "active"


def test_acquire_stores_reason_codes(repo):
    lease = repo.acquire(
        heuristic_id="h1", version="1.0.0", domain="tui_snake", context_hash="x",
        reason_codes=["fallback:ai_timeout", "context_changed"],
    )
    assert "fallback:ai_timeout" in lease.reason_codes
    assert "context_changed" in lease.reason_codes


def test_acquire_stores_selected_by(repo):
    lease = repo.acquire(
        heuristic_id="h1", version="1.0.0", domain="tui_snake", context_hash="x",
        selected_by="operator",
    )
    assert lease.selected_by == "operator"


# ── get_active ────────────────────────────────────────────────────────────────

def test_get_active_returns_live_lease(repo):
    repo.acquire(heuristic_id="h1", version="1.0.0", domain="tui_snake", context_hash="x")
    active = repo.get_active("tui_snake")
    assert active is not None
    assert active.status == "active"


def test_get_active_returns_none_when_expired(repo):
    past_ts = time.time() - 100
    lease = repo.acquire(heuristic_id="h1", version="1.0.0", domain="tui_snake", context_hash="x", ttl_seconds=5.0)
    # Simulate past deadline
    lease.deadline_at = past_ts
    repo.save(lease)

    active = repo.get_active("tui_snake")
    assert active is None


def test_get_active_returns_none_for_empty_domain(repo):
    assert repo.get_active("eclipse_snake") is None


def test_get_active_isolates_domains(repo):
    repo.acquire(heuristic_id="snake-h", version="1.0.0", domain="tui_snake", context_hash="a")
    assert repo.get_active("eclipse_snake") is None
    assert repo.get_active("chat_codecompass") is None


# ── release ───────────────────────────────────────────────────────────────────

def test_release_marks_lease_released(repo):
    lease = repo.acquire(heuristic_id="h1", version="1.0.0", domain="tui_snake", context_hash="x")
    released = repo.release(lease.id)
    assert released.status == "released"
    assert released.released_at is not None


def test_release_idempotent_when_already_released(repo):
    lease = repo.acquire(heuristic_id="h1", version="1.0.0", domain="tui_snake", context_hash="x")
    repo.release(lease.id)
    result = repo.release(lease.id)  # second call
    assert result.status == "released"


def test_release_returns_none_for_unknown_id(repo):
    assert repo.release("does-not-exist") is None


def test_release_accepts_custom_status(repo):
    lease = repo.acquire(heuristic_id="h1", version="1.0.0", domain="tui_snake", context_hash="x")
    result = repo.release(lease.id, status="expired")
    assert result.status == "expired"


# ── list_expired ──────────────────────────────────────────────────────────────

def test_list_expired_finds_overdue_leases(repo):
    lease = repo.acquire(heuristic_id="h1", version="1.0.0", domain="tui_snake", context_hash="x", ttl_seconds=5.0)
    lease.deadline_at = time.time() - 1
    repo.save(lease)

    expired = repo.list_expired()
    ids = [l.id for l in expired]
    assert lease.id in ids


def test_list_expired_excludes_live_leases(repo):
    repo.acquire(heuristic_id="h1", version="1.0.0", domain="tui_snake", context_hash="x", ttl_seconds=60.0)
    assert repo.list_expired() == []


def test_mark_expired_batch_updates_status(repo):
    lease = repo.acquire(heuristic_id="h1", version="1.0.0", domain="tui_snake", context_hash="x", ttl_seconds=5.0)
    lease.deadline_at = time.time() - 1
    repo.save(lease)

    count = repo.mark_expired_batch()
    assert count == 1

    refreshed = repo.get_by_id(lease.id)
    assert refreshed.status == "expired"


# ── list helpers ──────────────────────────────────────────────────────────────

def test_list_by_domain(repo):
    repo.acquire(heuristic_id="h1", version="1.0.0", domain="tui_snake", context_hash="a")
    repo.acquire(heuristic_id="h2", version="1.0.0", domain="tui_snake", context_hash="b")
    repo.acquire(heuristic_id="c1", version="1.0.0", domain="chat_codecompass", context_hash="c")

    snake_leases = repo.list_by_domain("tui_snake")
    assert len(snake_leases) == 2  # first was superseded but still exists
    chat_leases = repo.list_by_domain("chat_codecompass")
    assert len(chat_leases) == 1


def test_list_all(repo):
    for i in range(3):
        repo.acquire(heuristic_id=f"h{i}", version="1.0.0", domain="tui_snake", context_hash=str(i))
    all_leases = repo.list_all()
    assert len(all_leases) == 3
