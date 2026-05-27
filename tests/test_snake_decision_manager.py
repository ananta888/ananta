"""Tests for SnakeDecisionManager — T04.01 + T04.02."""
from __future__ import annotations

import time

import pytest
from sqlmodel import SQLModel, Session, delete

from agent.database import engine
from agent.db_models import DecisionTraceDB, HeuristicDecisionLeaseDB
from agent.repositories.decision_trace_repo import DecisionTraceRepository
from agent.repositories.heuristic_lease_repo import HeuristicLeaseRepository
from agent.services.heuristic_runtime.decision_context import DecisionContext
from agent.services.heuristic_runtime.heuristic_registry_service import HeuristicDefinition, HeuristicRegistry
from agent.services.heuristic_runtime.snake_decision_manager import (
    FollowWithDistanceCommand,
    LurkNearCommand,
    SnakeDecisionManager,
)


@pytest.fixture(autouse=True)
def _fresh_db():
    SQLModel.metadata.create_all(engine)
    yield
    with Session(engine) as s:
        s.exec(delete(HeuristicDecisionLeaseDB))
        s.exec(delete(DecisionTraceDB))
        s.commit()


def _make_heuristic(hid="snake-follow-v1", domain="tui_snake"):
    return HeuristicDefinition(
        heuristic_id=hid, version="1.0.0", domain=domain,
        strategy_kind="follow", description="test", deterministic=True,
        safety_class="bounded", capabilities=(), inputs=(), outputs=(), parameters={},
    )


def _make_registry(*heuristics):
    reg = HeuristicRegistry(base_path="/nonexistent")
    reg._loaded = True
    for h in heuristics:
        reg._all.append(h)
        reg._definitions[h.heuristic_id] = h
    return reg


def _ctx(surface="tui_snake", ai_status="offline", goal=None, panel=None):
    return DecisionContext(source_surface=surface, ai_status=ai_status, active_goal_id=goal, active_panel=panel)


def _make_manager(heuristics=None, domain="tui_snake"):
    h = heuristics or [_make_heuristic(domain=domain)]
    reg = _make_registry(*h)
    lease_repo = HeuristicLeaseRepository()
    trace_repo = DecisionTraceRepository()
    return SnakeDecisionManager(registry=reg, lease_repo=lease_repo, trace_repo=trace_repo)


# ── decide ────────────────────────────────────────────────────────────────────

def test_decide_returns_heuristic_result_when_ai_offline():
    mgr = _make_manager()
    result = mgr.decide(_ctx(ai_status="offline"))
    assert result.source == "heuristic"


def test_decide_returns_ai_path_when_ai_available():
    mgr = _make_manager()
    result = mgr.decide(_ctx(ai_status="available"))
    assert result.source == "ai"
    assert "ai_available" in result.reason_codes


def test_decide_lurks_without_goal():
    mgr = _make_manager()
    result = mgr.decide(_ctx(ai_status="offline"))
    assert result.action_kind == "lurk"


def test_decide_follows_toward_artifact_event():
    mgr = _make_manager()
    ctx = DecisionContext(
        source_surface="tui_snake",
        ai_status="offline",
        recent_events=[{"kind": "artifact_select", "normalized_value": "ref1"}],
    )
    result = mgr.decide(ctx)
    assert result.action_kind == "follow"


def test_decide_persists_trace(tmp_path):
    mgr = _make_manager()
    mgr.decide(_ctx(ai_status="offline"))
    rows = mgr._trace_repo.list_by_surface("tui_snake")
    assert len(rows) >= 1


# ── state machine integration ─────────────────────────────────────────────────

def test_initial_state_is_lurking():
    mgr = _make_manager()
    assert mgr.state_name == "lurking"


def test_send_ai_request_transitions_to_waiting():
    mgr = _make_manager()
    ctx = _ctx(ai_status="offline")
    mgr.decide(ctx)
    mgr.send_ai_event("ai_request_sent")
    assert mgr.state_name == "waiting_ai"


def test_ai_timeout_triggers_fallback_active():
    mgr = _make_manager()
    mgr.send_ai_event("ai_request_sent")
    mgr.send_ai_event("ai_timeout")
    assert mgr.state_name == "fallback_active"


def test_offline_ai_status_transitions_to_fallback():
    mgr = _make_manager()
    mgr.send_ai_event("ai_request_sent")
    mgr.decide(_ctx(ai_status="offline"))
    assert mgr.state_name == "fallback_active"


def test_is_fallback_active_property():
    mgr = _make_manager()
    assert not mgr.is_fallback_active
    mgr.send_ai_event("ai_request_sent")
    mgr.send_ai_event("ai_timeout")
    assert mgr.is_fallback_active


# ── lease management ──────────────────────────────────────────────────────────

def test_get_current_lease_returns_none_when_ai_available():
    mgr = _make_manager()
    mgr.decide(_ctx(ai_status="available"))
    assert mgr.get_current_lease("tui_snake") is None


def test_get_current_lease_returns_lease_when_offline():
    mgr = _make_manager()
    mgr.decide(_ctx(ai_status="offline"))
    lease = mgr.get_current_lease("tui_snake")
    assert lease is not None
    assert lease.status == "active"


# ── tick / expiry ─────────────────────────────────────────────────────────────

def test_tick_sweeps_expired_leases():
    mgr = _make_manager()
    mgr.decide(_ctx(ai_status="offline"))
    lease = mgr.get_current_lease("tui_snake")
    assert lease is not None
    lease.deadline_at = time.time() - 1
    mgr._lease_repo.save(lease)
    mgr.tick()
    expired = mgr._lease_repo.get_by_id(lease.id)
    assert expired.status == "expired"


# ── metrics ───────────────────────────────────────────────────────────────────

def test_metrics_accumulate():
    mgr = _make_manager()
    mgr.decide(_ctx(ai_status="offline"))
    mgr.decide(_ctx(ai_status="offline"))
    m = mgr.get_metrics()
    assert "tui_snake" in m
    assert m["tui_snake"]["total"] == 2


# ── FollowWithDistanceCommand ─────────────────────────────────────────────────

def test_follow_with_distance_command():
    cmd = FollowWithDistanceCommand(dx=1, dy=0)
    result = cmd.to_decision_result()
    assert result.action_kind == "follow"
    assert result.suggested_motion.dx == 1


# ── LurkNearCommand ───────────────────────────────────────────────────────────

def test_lurk_near_command():
    cmd = LurkNearCommand(lurk_zone_radius=3)
    result = cmd.to_decision_result()
    assert result.action_kind == "lurk"
    assert "lurk_near" in result.strategy_id
