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
    monkeypatch.setattr("agent.services.autopilot.loop_manager._background_threads_disabled", lambda _app=None: True)

    assert runtime.start_autopilot_watchdog(app) is None
    assert runtime.WATCHDOG_EXTENSION_KEY not in app.extensions


def test_start_autopilot_watchdog_is_idempotent(monkeypatch):
    app = _FakeApp()
    monkeypatch.setattr("agent.services.autopilot.loop_manager._background_threads_disabled", lambda _app=None: False)
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


class _FakeLoop:
    def __init__(self):
        self.running = False
        self.starts: list = []

    def start(self, **kwargs):
        self.running = True
        self.starts.append(kwargs)


def _coordinator_with(*items, session=None):
    """A minimal coordinator double: policy root, journal state, session gate."""
    from pathlib import Path
    from tempfile import TemporaryDirectory

    holder = TemporaryDirectory()
    coordinator = SimpleNamespace(
        policy=SimpleNamespace(
            root=Path(holder.name),
            value={"max_seconds": 0, "max_tasks": 0, "team_id": "team-1"},
            max_concurrency=lambda: 3,
        ),
        journal=SimpleNamespace(
            read=lambda: {
                "session": session or {"enabled": True, "expires_at": None, "dispatched": 0},
                "items": {f"i{n}": {"status": status} for n, status in enumerate(items)},
            }
        ),
    )
    coordinator._tmpdir = holder
    return coordinator


def test_a_test_running_item_keeps_the_autopilot_armed(monkeypatch):
    """A lane round in flight is still work: the loop must (re)arm for it.

    The watchdog used to require ``ready``/``running`` only, so a Hub restart
    during a test lane left the loop dead: no report was ever collected, no
    stale propose was ever reaped, and the lane task sat in ``proposing``
    indefinitely. Every pending item status must keep the loop armed.
    """
    from agent.services.autopilot import loop_manager

    app = _FakeApp()
    fake_loop = _FakeLoop()
    coordinator = _coordinator_with("test_running")
    monkeypatch.setattr(runtime, "get_development_coordinator", lambda: coordinator)
    monkeypatch.setattr(loop_manager, "autonomous_loop", fake_loop)

    assert runtime.ensure_autopilot(app) is True
    assert fake_loop.running is True and len(fake_loop.starts) == 1
    assert fake_loop.starts[0]["team_id"] == "team-1"
    coordinator._tmpdir.cleanup()


def test_every_pending_item_status_rearms_but_terminal_ones_do_not(monkeypatch):
    from agent.services.autopilot import loop_manager
    from agent.services.self_development.coordinator import PENDING_ITEM_STATUSES

    app = _FakeApp()

    for status in sorted(PENDING_ITEM_STATUSES):
        fake_loop = _FakeLoop()
        coordinator = _coordinator_with(status)
        monkeypatch.setattr(runtime, "get_development_coordinator", lambda: coordinator)
        monkeypatch.setattr(loop_manager, "autonomous_loop", fake_loop)

        assert runtime.ensure_autopilot(app) is True, status
        assert fake_loop.running is True, status
        coordinator._tmpdir.cleanup()

    for status in ("published", "failed", "blocked", "skipped", "cancelled"):
        fake_loop = _FakeLoop()
        coordinator = _coordinator_with(status)
        monkeypatch.setattr(runtime, "get_development_coordinator", lambda: coordinator)
        monkeypatch.setattr(loop_manager, "autonomous_loop", fake_loop)

        assert runtime.ensure_autopilot(app) is False, status
        assert fake_loop.running is False, status
        coordinator._tmpdir.cleanup()
