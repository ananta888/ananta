"""Tests for DecisionTrace, DomainMetrics, DecisionTraceRepository — T08.01."""
from __future__ import annotations

import time

import pytest
from sqlmodel import SQLModel, Session, delete

from agent.database import engine
from agent.db_models import DecisionTraceDB
from agent.repositories.decision_trace_repo import DecisionTraceRepository
from agent.services.heuristic_runtime.decision_result import DecisionResult
from agent.services.heuristic_runtime.decision_trace import (
    DecisionMetricsAccumulator,
    DecisionTrace,
    DomainMetrics,
)


@pytest.fixture(autouse=True)
def _fresh_db():
    SQLModel.metadata.create_all(engine)
    yield
    with Session(engine) as s:
        s.exec(delete(DecisionTraceDB))
        s.commit()


# ── DecisionTrace ─────────────────────────────────────────────────────────────

def test_trace_to_dict():
    trace = DecisionTrace(surface="tui_snake", context_hash="abc", source="heuristic", action_kind="follow")
    d = trace.to_dict()
    assert d["surface"] == "tui_snake"
    assert d["context_hash"] == "abc"
    assert "event_id" in d
    assert d["duration_ms"] is None


def test_trace_resolve_sets_resolved_at():
    trace = DecisionTrace(surface="tui_snake")
    trace.resolve()
    assert trace.resolved_at is not None
    assert trace.duration_ms >= 0


def test_trace_duration_ms_computed():
    trace = DecisionTrace(surface="tui_snake", started_at=time.time() - 0.1)
    trace.resolve()
    assert trace.duration_ms >= 100  # at least 100ms


def test_trace_from_decision_result():
    result = DecisionResult.heuristic_follow(dx=1, dy=0, strategy_id="my-strat")
    result.fallback_reason = "ai_timeout"
    trace = DecisionTrace.from_decision_result(
        result, surface="tui_snake", context_hash="hash1", lease_id="lease-1"
    )
    assert trace.surface == "tui_snake"
    assert trace.strategy_id == "my-strat"
    assert trace.fallback_reason == "ai_timeout"
    assert trace.source == "heuristic"
    assert trace.action_kind == "follow"


def test_trace_no_sensitive_data():
    trace = DecisionTrace(surface="tui_snake", context_hash="sha256hash", source="heuristic")
    d = trace.to_dict()
    for key in ("normalized_value", "query_text", "file_content", "raw_payload"):
        assert key not in d


# ── DomainMetrics ─────────────────────────────────────────────────────────────

def test_metrics_records_ai_success():
    m = DomainMetrics(surface="tui_snake")
    m.record(DecisionTrace(surface="tui_snake", source="ai", action_kind="follow"))
    assert m.ai_success == 1
    assert m.total == 1


def test_metrics_records_ai_timeout():
    m = DomainMetrics(surface="tui_snake")
    m.record(DecisionTrace(surface="tui_snake", source="heuristic", fallback_reason="ai_timeout"))
    assert m.ai_timeout == 1


def test_metrics_records_ttl_expired():
    m = DomainMetrics(surface="tui_snake")
    m.record(DecisionTrace(surface="tui_snake", source="heuristic", fallback_reason="lease_expired"))
    assert m.ttl_expired == 1


def test_metrics_records_no_match():
    m = DomainMetrics(surface="tui_snake")
    m.record(DecisionTrace(surface="tui_snake", source="heuristic", action_kind="no_action", confidence=0.0))
    assert m.no_match == 1


def test_metrics_to_dict():
    m = DomainMetrics(surface="tui_snake")
    d = m.to_dict()
    for field in ("ai_success", "ai_timeout", "heuristic_fallback", "ttl_expired", "no_match"):
        assert field in d


# ── DecisionMetricsAccumulator ────────────────────────────────────────────────

def test_accumulator_creates_domain_on_first_record():
    acc = DecisionMetricsAccumulator()
    trace = DecisionTrace(surface="tui_snake", source="ai", action_kind="follow")
    acc.record(trace)
    m = acc.get("tui_snake")
    assert m.total == 1


def test_accumulator_separates_domains():
    acc = DecisionMetricsAccumulator()
    acc.record(DecisionTrace(surface="tui_snake", source="ai"))
    acc.record(DecisionTrace(surface="chat_codecompass", source="heuristic"))
    assert acc.get("tui_snake").total == 1
    assert acc.get("chat_codecompass").total == 1


def test_accumulator_reset_single_domain():
    acc = DecisionMetricsAccumulator()
    acc.record(DecisionTrace(surface="tui_snake", source="ai"))
    acc.reset("tui_snake")
    assert acc.get("tui_snake").total == 0


def test_accumulator_reset_all():
    acc = DecisionMetricsAccumulator()
    acc.record(DecisionTrace(surface="tui_snake", source="ai"))
    acc.record(DecisionTrace(surface="chat_codecompass", source="heuristic"))
    acc.reset()
    assert len(acc.all()) == 0


# ── DecisionTraceRepository ───────────────────────────────────────────────────

def test_repo_save_and_get():
    repo = DecisionTraceRepository()
    trace = DecisionTrace(surface="tui_snake", context_hash="h1", source="heuristic", action_kind="follow")
    trace.resolve()
    row = repo.save(trace)
    assert row.id == trace.event_id
    assert row.surface == "tui_snake"

    fetched = repo.get_by_id(trace.event_id)
    assert fetched is not None
    assert fetched.context_hash == "h1"


def test_repo_list_by_surface():
    repo = DecisionTraceRepository()
    for _ in range(3):
        t = DecisionTrace(surface="tui_snake", source="heuristic")
        t.resolve()
        repo.save(t)
    t2 = DecisionTrace(surface="chat_codecompass", source="ai")
    t2.resolve()
    repo.save(t2)

    snake_rows = repo.list_by_surface("tui_snake")
    assert len(snake_rows) == 3
    chat_rows = repo.list_by_surface("chat_codecompass")
    assert len(chat_rows) == 1


def test_repo_list_fallbacks():
    repo = DecisionTraceRepository()
    t1 = DecisionTrace(surface="tui_snake", source="heuristic", fallback_reason="ai_timeout")
    t1.resolve()
    t2 = DecisionTrace(surface="tui_snake", source="ai")
    t2.resolve()
    repo.save(t1)
    repo.save(t2)

    fallbacks = repo.list_fallbacks(surface="tui_snake")
    assert len(fallbacks) == 1
    assert fallbacks[0].fallback_reason == "ai_timeout"


def test_repo_count_by_source():
    repo = DecisionTraceRepository()
    for src in ("ai", "ai", "heuristic"):
        t = DecisionTrace(surface="tui_snake", source=src)
        t.resolve()
        repo.save(t)

    counts = repo.count_by_source("tui_snake")
    assert counts["ai"] == 2
    assert counts["heuristic"] == 1
