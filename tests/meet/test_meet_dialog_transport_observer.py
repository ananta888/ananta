"""Observe native end-to-end exchange without requests, retries or private data."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tests.meet_dialog_transport_observer import DialogTransportObserver
from worker.meet_media.dialog_client import HubDialogClient


def test_native_call_is_observed_while_inflight_then_completed_without_body_retention(monkeypatch):
    clock = SimpleNamespace(now=100.0)
    result = {"private": "result"}
    seen = []

    def native(client, action, **fields):
        assert action == "exchange" and fields == {"meet_session_id": "private-session"}
        clock.now = 101.6
        seen.append(observer.report())
        return result

    monkeypatch.setattr(HubDialogClient, "call", native)
    observer = DialogTransportObserver(monkeypatch, clock=lambda: clock.now)
    assert HubDialogClient.call(object(), "exchange", meet_session_id="private-session") is result
    assert seen == [{"recent": [], "inflight": [{"started_at_ms": 100000.0, "elapsed_ms": 1600.0}], "overflow": False}]
    assert observer.report() == {
        "recent": [{"status": "completed", "started_at_ms": 100000.0, "elapsed_ms": 1600.0}],
        "inflight": [],
        "overflow": False,
    }
    observer.report()["recent"][0]["status"] = "modified"
    assert observer.report()["recent"][0]["status"] == "completed"


def test_observation_retains_12_results_and_preserves_unknown_error_identity(monkeypatch):
    error = RuntimeError("private transport error")
    native = Mock(side_effect=error)
    monkeypatch.setattr(HubDialogClient, "call", native)
    observer = DialogTransportObserver(monkeypatch, clock=lambda: 100)
    for _ in range(15):
        with pytest.raises(RuntimeError) as caught:
            HubDialogClient.call(object(), "exchange", private="content")
        assert caught.value is error
    report = observer.report()
    assert native.call_count == 15 and len(report["recent"]) == 12 and report["inflight"] == []
    assert all(row == {"status": "failed", "started_at_ms": 100000.0, "elapsed_ms": 0.0} for row in report["recent"])
    assert "private" not in str(report)


def test_unrelated_calls_pass_through_without_clock_or_observation(monkeypatch):
    native, clock = Mock(return_value=object()), Mock(side_effect=AssertionError("not an exchange"))
    monkeypatch.setattr(HubDialogClient, "call", native)
    observer = DialogTransportObserver(monkeypatch, clock=clock)
    client = object()
    assert HubDialogClient.call(client, "finish", status="failed") is native.return_value
    native.assert_called_once_with(client, "finish", status="failed")
    clock.assert_not_called()
    assert not observer.active and not observer.recent


def test_even_overlapping_calls_cannot_expand_the_observer_or_change_dispatch(monkeypatch):
    depth, snapshots = 0, []

    def native(client, action, **fields):
        nonlocal depth
        depth += 1
        if depth < 8:
            HubDialogClient.call(client, action, **fields)
        snapshots.append(observer.report())
        return True

    monkeypatch.setattr(HubDialogClient, "call", native)
    observer = DialogTransportObserver(monkeypatch, clock=lambda: 100)
    assert HubDialogClient.call(object(), "exchange", private="not recorded") is True
    assert depth == 8 and all(len(row["inflight"]) <= 4 for row in snapshots)
    assert observer.report()["overflow"] and not observer.report()["inflight"]
