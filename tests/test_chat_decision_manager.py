"""Tests for ChatDecisionManager — T05.01."""
from __future__ import annotations

import pytest
from sqlmodel import SQLModel, Session, delete

from agent.database import engine
from agent.db_models import DecisionTraceDB, HeuristicDecisionLeaseDB
from agent.repositories.decision_trace_repo import DecisionTraceRepository
from agent.repositories.heuristic_lease_repo import HeuristicLeaseRepository
from agent.services.heuristic_runtime.chat_decision_manager import ChatDecisionManager
from agent.services.heuristic_runtime.decision_context import DecisionContext
from agent.services.heuristic_runtime.heuristic_registry_service import HeuristicDefinition, HeuristicRegistry


@pytest.fixture(autouse=True)
def _fresh_db():
    SQLModel.metadata.create_all(engine)
    yield
    with Session(engine) as s:
        s.exec(delete(HeuristicDecisionLeaseDB))
        s.exec(delete(DecisionTraceDB))
        s.commit()


def _make_heuristic():
    return HeuristicDefinition(
        heuristic_id="chat-select-v1", version="1.0.0", domain="chat_codecompass",
        strategy_kind="context_select", description="test", deterministic=True,
        safety_class="bounded", capabilities=(), inputs=(), outputs=(), parameters={},
    )


def _make_manager(policy_check=None):
    reg = HeuristicRegistry(base_path="/nonexistent")
    reg._loaded = True
    h = _make_heuristic()
    reg._all.append(h)
    reg._definitions[h.heuristic_id] = h
    lease_repo = HeuristicLeaseRepository()
    trace_repo = DecisionTraceRepository()
    mgr = ChatDecisionManager(lease_repo=lease_repo, trace_repo=trace_repo, policy_check=policy_check)
    mgr._registry = reg
    from agent.services.heuristic_runtime.lease_reevaluation_service import LeaseReevaluationService
    mgr._reeval = LeaseReevaluationService(repo=lease_repo, registry=reg)
    return mgr


def _ctx(ai_status="offline", goal=None, artifacts=None):
    return DecisionContext(
        source_surface="chat_codecompass",
        ai_status=ai_status,
        active_goal_id=goal,
        selected_artifacts=artifacts or [],
    )


# ── basic decide ──────────────────────────────────────────────────────────────

def test_decide_heuristic_when_ai_offline():
    mgr = _make_manager()
    result = mgr.decide("was macht diese Datei", _ctx(ai_status="offline"))
    assert result.source == "heuristic"


def test_decide_ai_path_when_ai_available():
    mgr = _make_manager()
    result = mgr.decide("explain this", _ctx(ai_status="available"))
    assert result.source == "ai"


def test_decide_with_selected_artifact_returns_context_summary():
    mgr = _make_manager()
    result = mgr.decide("erkläre", _ctx(ai_status="offline", artifacts=["src/main.py"]))
    assert result.answer_kind == "context_summary"
    assert "src/main.py" in result.selected_context_refs


def test_decide_no_context_returns_no_good_match():
    mgr = _make_manager()
    result = mgr.decide("xyzqqqq", _ctx(ai_status="offline"))
    assert result.is_no_good_match()


def test_decide_enriches_with_intent_reason_code():
    mgr = _make_manager()
    result = mgr.decide("wo ist MyClass definiert", _ctx(ai_status="offline"))
    assert any("intent:" in rc for rc in result.reason_codes)


# ── policy gate ───────────────────────────────────────────────────────────────

def test_policy_denied_blocks_decision():
    def deny_all(q, ctx): return False, "blocked_by_policy"
    mgr = _make_manager(policy_check=deny_all)
    result = mgr.decide("anything", _ctx())
    assert result.action_kind == "policy_denied"
    assert "blocked_by_policy" in result.reason_codes


def test_policy_allowed_proceeds():
    def allow_all(q, ctx): return True, ""
    mgr = _make_manager(policy_check=allow_all)
    result = mgr.decide("was macht das", _ctx(ai_status="offline"))
    assert result.action_kind != "policy_denied"


# ── late AI response ──────────────────────────────────────────────────────────

def test_late_ai_response_discarded_when_context_changed():
    mgr = _make_manager()
    ctx1 = _ctx(ai_status="available")
    mgr.decide("explain", ctx1)  # sets last_context_hash = ctx1.context_hash

    from agent.services.heuristic_runtime.decision_result import DecisionResult
    ai_result = DecisionResult(action_kind="chat", confidence=0.9, source="ai")

    # Simulate context change — use a different context
    ctx2 = DecisionContext(source_surface="chat_codecompass", ai_status="offline", active_goal_id="new_goal")
    mgr.decide("new query", ctx2)

    accepted = mgr.handle_late_ai_response(ai_result, original_context_hash=ctx1.context_hash)
    assert not accepted


def test_late_ai_response_accepted_when_still_waiting():
    mgr = _make_manager()
    ctx = _ctx(ai_status="available")
    mgr.decide("explain", ctx)  # ai available → waiting_ai state

    from agent.services.heuristic_runtime.decision_result import DecisionResult
    ai_result = DecisionResult(action_kind="chat", confidence=0.9, source="ai")
    accepted = mgr.handle_late_ai_response(ai_result, original_context_hash=ctx.context_hash)
    assert accepted


# ── state machine ─────────────────────────────────────────────────────────────

def test_initial_state_is_waiting_ai():
    mgr = _make_manager()
    assert mgr.state_name == "waiting_ai"


def test_state_transitions_to_heuristic_on_offline():
    mgr = _make_manager()
    # query with artifact → SelectedArtifactSelector handles it → heuristic_answer_ready
    mgr.decide("erkläre", _ctx(ai_status="offline", artifacts=["src/main.py"]))
    assert mgr.state_name == "heuristic_answer_ready"


# ── metrics ───────────────────────────────────────────────────────────────────

def test_metrics_accumulate_after_decisions():
    mgr = _make_manager()
    mgr.decide("erkläre", _ctx(ai_status="offline"))
    mgr.decide("error?", _ctx(ai_status="offline"))
    m = mgr.get_metrics()
    assert "chat_codecompass" in m
    assert m["chat_codecompass"]["total"] == 2


# ── trace persistence ─────────────────────────────────────────────────────────

def test_trace_persisted_after_decide():
    mgr = _make_manager()
    mgr.decide("erkläre", _ctx(ai_status="offline"))
    rows = mgr._trace_repo.list_by_surface("chat_codecompass")
    assert len(rows) >= 1
    assert rows[0].surface == "chat_codecompass"
