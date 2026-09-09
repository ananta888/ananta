"""Startup ordering is observation only: no inferred join or suppressed failure."""

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
    assert observer.snapshot() == {"phase": "joined", "joined_ms": 1500}
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
    assert observer.snapshot() == {"phase": "failed", "joined_ms": None}
