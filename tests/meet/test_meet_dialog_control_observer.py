"""Diagnostic observation must not consume, retry or change control execution."""

from concurrent.futures import Future
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tests.meet_dialog_control_observer import DialogControlObserver
from worker.meet_media.dialog_control_exchange import DialogControlExchange


def test_failure_snapshot_is_bounded_copy_without_private_hub_inputs(monkeypatch):
    observer = DialogControlObserver(monkeypatch)
    clock = SimpleNamespace(now=100.0)
    pending = Future()
    pool = Mock()
    pool.submit.return_value = pending
    exchange = DialogControlExchange(Mock(), "private-session", clock=lambda: clock.now, pool=pool)
    assert exchange.poll() is None
    assert observer.report() is None
    clock.now = 102.6
    pending.set_result({"private": "unconsumed-result"})
    with pytest.raises(ValueError, match="request_stale"):
        exchange.poll()
    report = observer.report()
    assert report == {
        "poll_gap_ms": 2600.0,
        "expiry_delta_ms": None,
        "request_age_ms": 2600.0,
        "pending": True,
        "done": True,
        "running": False,
        "next_due_delta_ms": 102600.0,
    }
    assert exchange.pending is pending and pool.submit.call_count == 1
    report.clear()
    assert observer.report() and "private" not in str(observer.report())
    assert observer.history() == [
        {
            "event": "poll",
            "at_ms": 100000.0,
            "request_started_ms": 100000.0,
            "fresh_until_ms": None,
            "pending": True,
            "done": False,
        },
        {
            "event": "failed",
            "at_ms": 102600.0,
            "request_started_ms": 100000.0,
            "fresh_until_ms": None,
            "pending": True,
            "done": True,
        },
    ]
    exchange.close()


def test_transition_history_is_bounded_and_does_not_record_every_pcm_tick(monkeypatch):
    observer = DialogControlObserver(monkeypatch)
    clock = SimpleNamespace(now=100.0)
    pool = Mock()
    pool.submit.side_effect = lambda *args, **kwargs: Future()
    exchange = DialogControlExchange(Mock(), "private-session", clock=lambda: clock.now, pool=pool)
    for _ in range(20):
        exchange.poll()
        pending = exchange.pending
        for _ in range(100):
            assert exchange.poll() is None
        pending.set_result({"private": "result"})
        exchange.poll()
        clock.now += 1
    assert pool.submit.call_count == 20 and observer.report() is None
    history = observer.history()
    assert len(history) == 16 and {row["event"] for row in history} == {"poll", "accepted"}
    history[-1]["event"] = "modified"
    assert observer.history()[-1]["event"] == "accepted"
    assert "private" not in str(observer.history())
    exchange.close()
