"""Observation must preserve errors/results and never retain request contents."""

from unittest.mock import Mock

import pytest

from agent.services.meet_contract import MeetError
from agent.services.meet_dialog_service import MeetDialogService
from tests.meet_dialog_callback_observer import DialogCallbackObserver


def test_observer_keeps_only_bounded_success_timing_and_lease_generation(monkeypatch):
    response = {"authorization": {"lease": {"generation": 2}}, "renewal": "synthetic-private-grant"}
    original = Mock(return_value=response)
    monkeypatch.setattr(MeetDialogService, "exchange", original)
    observer = DialogCallbackObserver(monkeypatch)
    service = object()
    for _ in range(15):
        assert MeetDialogService.exchange(service, {"secret": "synthetic-private-request"}) is response
    records = observer.report()
    assert len(records) == 12
    assert all(set(row) == {"status", "generation", "renewal", "started_at_ms", "elapsed_ms"} for row in records)
    assert all(row["status"] == "ok" and row["generation"] == 2 and row["renewal"] is True for row in records)
    assert "private" not in str(records)


def test_observer_does_not_hide_or_replace_actual_hub_denials(monkeypatch):
    error = MeetError("meet_authorization_unavailable", 503)
    monkeypatch.setattr(MeetDialogService, "exchange", Mock(side_effect=error))
    observer = DialogCallbackObserver(monkeypatch)
    with pytest.raises(MeetError) as raised:
        MeetDialogService.exchange(object(), {"secret": "synthetic"})
    assert raised.value is error
    assert observer.report()[0] | {"elapsed_ms": 0, "started_at_ms": 0} == {
        "status": "failed",
        "code": error.code,
        "http_status": 503,
        "elapsed_ms": 0,
        "started_at_ms": 0,
    }


def test_domain_timing_is_correlated_copied_and_unknown_failure_codes_are_redacted(monkeypatch):
    error = MeetError("private-dynamic-code", 503)
    monkeypatch.setattr(MeetDialogService, "exchange", Mock(side_effect=error))
    ticks = iter([100.0, 100.05])
    observer = DialogCallbackObserver(monkeypatch, clock=lambda: next(ticks))
    with pytest.raises(MeetError) as caught:
        MeetDialogService.exchange(object(), {"private": "not recorded"})
    assert caught.value is error
    assert observer.report() == [
        {"status": "failed", "code": "redacted", "http_status": 503, "started_at_ms": 100000.0, "elapsed_ms": 50.0}
    ]
    observer.report()[0]["code"] = "modified"
    assert observer.report()[0]["code"] == "redacted"
