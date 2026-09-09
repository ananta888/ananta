"""Startup ordering is observation only: no inferred join or suppressed failure."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tests.meet_dialog_startup_observer import DialogStartupObserver
from worker.meet_media.dialog_session_operations import DialogSessionOperations


def test_real_return_precedes_join_observation_and_arguments_are_unchanged(monkeypatch):
    ready, join = Mock(), Mock(return_value="actual")
    monkeypatch.setattr(DialogSessionOperations, "ready", ready)
    monkeypatch.setattr(DialogSessionOperations, "join", join)
    now = [1.0]
    observer = DialogStartupObserver(monkeypatch, clock=lambda: now[0])
    observer.start()
    session = object.__new__(DialogSessionOperations)
    session.ready(["chat.read"])
    ready.assert_called_once_with(session, ["chat.read"])
    assert not observer.settled.is_set()
    now[0] = 2.5
    assert session.join("room", "grant") == "actual"
    join.assert_called_once_with(session, "room", "grant")
    assert observer.settled.is_set()
    assert observer.snapshot() == {
        "phase": "joined",
        "failed_phase": None,
        "joined_ms": 1500,
        "http_errors": [],
        "request_errors": [],
        "script_errors": [],
    }
    observer.finished()
    assert observer.snapshot()["phase"] == "joined"


@pytest.mark.parametrize("stage", ["bootstrap", "ready", "join"])
def test_failure_wakes_waiter_without_promoting_startup_or_exposing_error(monkeypatch, stage):
    error = RuntimeError("PRIVATE")
    monkeypatch.setattr(DialogSessionOperations, "ready", Mock(side_effect=error if stage == "ready" else None))
    monkeypatch.setattr(DialogSessionOperations, "join", Mock(side_effect=error if stage == "join" else None))
    observer = DialogStartupObserver(monkeypatch)
    observer.start()
    session = object.__new__(DialogSessionOperations)
    if stage != "bootstrap":
        with pytest.raises(RuntimeError) as caught:
            getattr(session, stage)()
        assert caught.value is error
    assert not observer.settled.is_set()
    observer.finished()
    assert observer.settled.is_set()
    assert observer.snapshot() == {
        "phase": "failed",
        "joined_ms": None,
        "failed_phase": {"bootstrap": "browser_bootstrap", "ready": "client_ready", "join": "joining"}[stage],
        "http_errors": [],
        "request_errors": [],
        "script_errors": [],
    }


def test_startup_transport_observation_is_bounded_redacted_and_copied(monkeypatch):
    observer = DialogStartupObserver(monkeypatch)
    handlers, page_handlers = {}, {}
    observer.observe_context(SimpleNamespace(on=lambda name, callback: handlers.__setitem__(name, callback)))
    handlers["page"](SimpleNamespace(on=lambda name, callback: page_handlers.__setitem__(name, callback)))
    for _ in range(20):
        handlers["response"](SimpleNamespace(status=503))
        handlers["response"](SimpleNamespace(status=True))
        handlers["requestfailed"](SimpleNamespace(failure="PRIVATE URL net::ERR_FAILED PRIVATE"))
        page_handlers["pageerror"](RuntimeError("PRIVATE TOKEN"))
    value = observer.snapshot()
    assert value["http_errors"] == [503] * 8
    assert value["request_errors"] == ["ERR_FAILED"] * 8
    assert value["script_errors"] == ["unclassified"] * 8
    value["http_errors"].clear()
    assert len(observer.snapshot()["http_errors"]) == 8
