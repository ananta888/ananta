"""Startup ordering is observation only: no inferred join or suppressed failure."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tests.meet_dialog_startup_observer import DialogStartupObserver, require_observed_dialog_startup
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
        "fetch_errors": [],
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
        "fetch_errors": [],
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


@pytest.mark.parametrize(
    "message,code",
    [
        ("PRIVATE certificate failure", "certificate"),
        ("PRIVATE Timeout 2000ms", "timeout"),
        ("PRIVATE ECONNRESET", "connection"),
        ("PRIVATE", "unclassified"),
    ],
)
def test_fetch_observer_preserves_exception_and_call_without_logging_payload(monkeypatch, message, code):
    from playwright.sync_api import Route

    error = RuntimeError(message)
    fetch = Mock(side_effect=error)
    monkeypatch.setattr(Route, "fetch", fetch)
    observer = DialogStartupObserver(monkeypatch)
    route = object.__new__(Route)
    for _ in range(10):
        with pytest.raises(RuntimeError) as caught:
            route.fetch(timeout=2000, max_redirects=0, max_retries=0)
        assert caught.value is error
    fetch.assert_called_with(route, timeout=2000, max_redirects=0, max_retries=0)
    assert observer.snapshot()["fetch_errors"] == [code] * 8


@pytest.mark.parametrize(
    "resources",
    [
        {"members": 1, "machines": 0, "connectionDrops": 8},
        {"PRIVATE": "TOKEN"},
        {"members": True, "machines": 0, "connectionDrops": 0},
    ],
)
def test_startup_failure_inspects_only_closed_peer_counts_and_never_claims_join(resources):
    observer, record = Mock(), Mock()
    observer.snapshot.return_value = {"phase": "failed"}
    command = Mock(return_value=resources)
    with pytest.raises(AssertionError):
        require_observed_dialog_startup(observer, Mock(), [], command, record)
    observer.settled.wait.assert_called_once_with(60)
    command.assert_called_once_with("fixture_resources")
    expected = (
        resources
        if resources.get("members") == 1 and type(resources.get("members")) is int
        else {"state": "unavailable"}
    )
    record.assert_called_with("dialog_startup_peer_resources", expected)


def test_confirmed_join_does_not_request_peer_diagnostics():
    observer, command, record = Mock(), Mock(), Mock()
    observer.snapshot.return_value = {"phase": "joined"}
    completed = Mock(is_set=lambda: False)
    require_observed_dialog_startup(observer, completed, [], command, record)
    command.assert_not_called()
    record.assert_called_once_with("dialog_startup", {"phase": "joined"})
