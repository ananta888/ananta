"""The self-development watchdog must re-arm the Autopilot while a session is active."""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

from agent.services.self_development import runtime


class _FakeApp:
    def __init__(self):
        self.extensions: dict = {}
        self.config: dict = {}
        self.logger = SimpleNamespace(warning=lambda *a, **k: None, info=lambda *a, **k: None)
        self.contexts = 0

    def app_context(self):
        app = self

        class _Ctx:
            def __enter__(self):
                app.contexts += 1
                return app

            def __exit__(self, *exc):
                return False

        return _Ctx()


def test_tick_rearms_within_an_app_context(monkeypatch):
    app = _FakeApp()
    calls = []
    monkeypatch.setattr(runtime, "ensure_autopilot", lambda a: calls.append(a) or True)

    assert runtime.AutopilotRearmWatchdog(app).tick() is True
    assert calls == [app] and app.contexts == 1


def test_start_autopilot_watchdog_skips_when_threads_disabled(monkeypatch):
    app = _FakeApp()
    monkeypatch.setattr("agent.routes.tasks.autopilot._background_threads_disabled", lambda _app=None: True)

    assert runtime.start_autopilot_watchdog(app) is None
    assert runtime.WATCHDOG_EXTENSION_KEY not in app.extensions


def test_start_autopilot_watchdog_is_idempotent(monkeypatch):
    app = _FakeApp()
    monkeypatch.setattr("agent.routes.tasks.autopilot._background_threads_disabled", lambda _app=None: False)
    monkeypatch.setattr(runtime, "ensure_autopilot", lambda _app: True)

    first = runtime.start_autopilot_watchdog(app)
    second = runtime.start_autopilot_watchdog(app)
    try:
        assert first is second is app.extensions[runtime.WATCHDOG_EXTENSION_KEY]
    finally:
        first.stop()


def test_watchdog_rearms_periodically(monkeypatch):
    app = _FakeApp()
    calls = {"n": 0}
    armed = threading.Event()

    def _arm(_app):
        calls["n"] += 1
        armed.set()
        return True

    monkeypatch.setattr(runtime, "ensure_autopilot", _arm)
    watchdog = runtime.AutopilotRearmWatchdog(app, interval_seconds=0.01)
    watchdog.start()
    try:
        assert armed.wait(2.0)
        deadline = time.time() + 2.0
        while calls["n"] < 2 and time.time() < deadline:
            time.sleep(0.01)
        assert calls["n"] >= 2
    finally:
        watchdog.stop()
