"""Lifecycle tests explicitly own every thread and never require interaction."""

import threading
from unittest.mock import Mock

import pytest
from flask import Flask

import agent.common.context
from agent.services.background.meet_dialog_deadlines import (
    EXTENSION,
    RUNNER,
    start_meet_dialog_deadlines,
    stop_meet_dialog_deadlines,
)


@pytest.mark.parametrize("role,testing,enabled", [("worker", False, True), ("hub", True, True), ("hub", False, False)])
def test_disabled_testing_or_worker_does_not_start(role, testing, enabled):
    app = Flask(__name__)
    app.config.update(ROLE=role, TESTING=testing)
    if enabled:
        app.extensions[RUNNER] = Mock()
    start_meet_dialog_deadlines(app)
    assert EXTENSION not in app.extensions


@pytest.mark.timeout(10)
def test_opted_in_hub_runs_once_in_app_context_stops_and_redacts_errors(monkeypatch, caplog):
    from flask import current_app

    app = Flask(__name__)
    app.config["ROLE"] = "hub"
    called = threading.Event()

    def run_once(*, limit, stopped):
        assert current_app._get_current_object() is app
        assert limit == 25 and not stopped()
        called.set()
        raise RuntimeError("private-credential-never-log")

    app.extensions[RUNNER] = Mock(run_once=run_once)
    monkeypatch.setattr(agent.common.context, "active_threads", [])
    try:
        start_meet_dialog_deadlines(app)
        assert called.wait(2)
        thread = app.extensions[EXTENSION]["thread"]
        start_meet_dialog_deadlines(app)
        assert agent.common.context.active_threads == [thread]
    finally:
        stop_meet_dialog_deadlines(app)
        app.extensions[EXTENSION]["thread"].join(timeout=2)
    assert not thread.is_alive()
    assert "RuntimeError" in caplog.text and "private-credential" not in caplog.text
    stop_meet_dialog_deadlines(app)
