"""Unit tests for AI-Snake TraceStore and redaction."""

from __future__ import annotations

import time

import pytest

from agent.routes.ai_snake_trace_store import (
    TraceRecorder,
    TraceStore,
    redact_value,
)


@pytest.fixture()
def store():
    return TraceStore(max_traces=5, max_events_per_trace=10, ttl_seconds=3600)


@pytest.fixture()
def recorder(store):
    trace_id = store.new_trace(snake_id="s1")
    return TraceRecorder(store, trace_id), store, trace_id


# ── TraceStore: basic operations ──────────────────────────────────────────────


def test_new_trace_creates_entry(store):
    tid = store.new_trace(snake_id="snake-1", session_id="sess-a")
    trace = store.get_trace(tid)
    assert trace is not None
    assert trace["trace_id"] == tid
    assert trace["snake_id"] == "snake-1"
    assert trace["session_id"] == "sess-a"
    assert trace["status"] == "running"


def test_add_event_sequential_seq(store):
    tid = store.new_trace()
    store.add_event(tid, {"phase": "request_received", "title": "A"})
    store.add_event(tid, {"phase": "llm_call_started", "title": "B"})
    events = store.get_events(tid, since_seq=0)
    assert len(events) == 2
    assert events[0]["seq"] == 0
    assert events[1]["seq"] == 1


def test_get_events_since_seq(store):
    tid = store.new_trace()
    for i in range(5):
        store.add_event(tid, {"phase": f"phase_{i}", "title": str(i)})
    events = store.get_events(tid, since_seq=3)
    assert len(events) == 2
    assert all(e["seq"] >= 3 for e in events)


def test_complete_trace_sets_status(store):
    tid = store.new_trace()
    store.complete_trace(tid, status="completed")
    trace = store.get_trace(tid)
    assert trace["status"] == "completed"
    assert trace["finished_at"] is not None


def test_complete_trace_failed(store):
    tid = store.new_trace()
    store.complete_trace(tid, status="failed")
    assert store.get_trace(tid)["status"] == "failed"


def test_max_events_per_trace_limit(store):
    tid = store.new_trace()
    for i in range(15):
        store.add_event(tid, {"phase": "test", "title": str(i)})
    events = store.get_events(tid, since_seq=0)
    assert len(events) == 10  # max_events_per_trace


def test_max_traces_eviction(store):
    ids = [store.new_trace(snake_id=f"s{i}") for i in range(5)]
    sixth = store.new_trace(snake_id="s6")
    traces = store.list_traces(limit=10)
    trace_ids = {t["trace_id"] for t in traces}
    assert sixth in trace_ids
    # Oldest evicted
    assert ids[0] not in trace_ids


def test_list_traces_sorted_newest_first(store):
    t1 = store.new_trace(snake_id="s1")
    time.sleep(0.01)
    t2 = store.new_trace(snake_id="s1")
    traces = store.list_traces(snake_id="s1")
    assert traces[0]["trace_id"] == t2
    assert traces[1]["trace_id"] == t1


def test_list_traces_filter_by_snake_id(store):
    store.new_trace(snake_id="alice")
    store.new_trace(snake_id="bob")
    alice_traces = store.list_traces(snake_id="alice")
    assert all(t["snake_id"] == "alice" for t in alice_traces)
    assert len(alice_traces) == 1


def test_ttl_eviction(store):
    store.ttl_seconds = 0
    tid = store.new_trace()
    # Force updated_at to be in the past
    store._traces[tid]["updated_at"] = time.time() - 10
    # Create a new trace to trigger eviction
    store.new_trace()
    assert store.get_trace(tid) is None


def test_get_trace_unknown_returns_none(store):
    assert store.get_trace("does-not-exist") is None


def test_get_events_unknown_trace(store):
    assert store.get_events("ghost", since_seq=0) == []


# ── TraceRecorder ─────────────────────────────────────────────────────────────


def test_recorder_writes_event(recorder):
    rec, store, trace_id = recorder
    rec.event("request_received", "Anfrage empfangen", summary="Hallo Welt", status="completed")
    events = store.get_events(trace_id, since_seq=0)
    assert len(events) == 1
    assert events[0]["phase"] == "request_received"
    assert events[0]["title"] == "Anfrage empfangen"
    assert events[0]["status"] == "completed"
    assert events[0]["summary"] == "Hallo Welt"


def test_recorder_failed_event_with_error(recorder):
    rec, store, trace_id = recorder
    rec.event("failed", "Fehler", status="failed", error="Etwas lief schief")
    events = store.get_events(trace_id, since_seq=0)
    assert events[0]["status"] == "failed"
    assert events[0]["error"] == "Etwas lief schief"


def test_recorder_tool_call_serializes_args_redacted(recorder):
    rec, store, trace_id = recorder
    rec.event(
        "tool_call_completed", "Tool ausgeführt",
        details={"tool": "search", "args": {"Authorization": "Bearer secret123", "query": "test"}},
    )
    events = store.get_events(trace_id, since_seq=0)
    details_str = str(events[0]["details"])
    assert "secret123" not in details_str
    assert "[REDACTED]" in details_str
    assert events[0]["redaction_applied"] is True


# ── Redaction ─────────────────────────────────────────────────────────────────


def test_redact_bearer_token():
    value, applied = redact_value("Authorization: Bearer eyJhbGciOiJSUzI1NiJ9.payload.sig")
    assert "eyJhbGciOiJSUzI1NiJ9" not in value
    assert "[REDACTED]" in value
    assert applied is True


def test_redact_api_key_string():
    value, applied = redact_value("api_key=sk-supersecret1234abcd")
    assert "supersecret1234abcd" not in value
    assert applied is True


def test_redact_clean_string_unchanged():
    value, applied = redact_value("Normaler Text ohne Secrets")
    assert value == "Normaler Text ohne Secrets"
    assert applied is False


def test_redact_dict_with_authorization():
    d = {"Authorization": "Bearer tok123xyz", "query": "test"}
    value, applied = redact_value(d)
    assert "tok123xyz" not in str(value)
    assert applied is True


def test_redact_long_string_truncated():
    long_str = "x" * 5000
    value, applied = redact_value(long_str, max_chars=4000)
    assert len(value) < 5100
    assert "Zeichen" in value


def test_redact_x_ananta_user_authorization():
    value, applied = redact_value("X-Ananta-User-Authorization: Bearer usertoken999")
    assert "usertoken999" not in str(value)
    assert applied is True
