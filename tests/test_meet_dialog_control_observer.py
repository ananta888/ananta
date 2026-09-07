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
    }
    assert exchange.pending is pending and pool.submit.call_count == 1
    report.clear()
    assert observer.report() and "private" not in str(observer.report())
    exchange.close()
