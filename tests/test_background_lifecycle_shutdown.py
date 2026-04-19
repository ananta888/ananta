from types import SimpleNamespace

import agent.common.context
import agent.lifecycle as lifecycle


class _FakeThread:
    def __init__(self):
        self.join_calls = 0

    def is_alive(self):
        return True

    def join(self, timeout=None):
        self.join_calls += 1
        self.timeout = timeout


def test_background_manager_contains_start_failures_and_records_state(monkeypatch):
    app = SimpleNamespace(testing=False, extensions={})
    manager = lifecycle.BackgroundServiceManager(app)

    monkeypatch.setattr(manager, "_is_testing", lambda: False)
    monkeypatch.setattr(manager, "_should_skip_for_reloader", lambda: False)
    monkeypatch.setattr(manager, "_start_registration", lambda: None)
    monkeypatch.setattr(manager, "_start_llm_monitoring", lambda: (_ for _ in ()).throw(RuntimeError("llm down")))
    monkeypatch.setattr(manager, "_start_monitoring", lambda: None)
    monkeypatch.setattr(manager, "_start_housekeeping", lambda: None)
    monkeypatch.setattr(manager, "_start_scheduler", lambda: None)
    monkeypatch.setattr(lifecycle.settings, "disable_llm_check", False)

    manager.start_all()

    assert manager.started_services == ["registration", "monitoring", "housekeeping", "scheduler"]
    assert manager.failed_services == {"llm_monitoring": "llm down"}
    assert app.extensions["background_services"]["failed"] == {"llm_monitoring": "llm down"}


def test_background_manager_shutdown_is_idempotent_and_joins_threads(monkeypatch):
    app = SimpleNamespace(testing=False, extensions={})
    thread = _FakeThread()
    manager = lifecycle.BackgroundServiceManager(app)

    monkeypatch.setattr(agent.common.context, "shutdown_requested", False)
    monkeypatch.setattr(agent.common.context, "active_threads", [thread])
    monkeypatch.setattr(manager, "_stop_scheduler", lambda: None)

    first = manager.shutdown(join_timeout=0.25)
    second = manager.shutdown(join_timeout=0.25)

    assert first["shutdown_requested"] is True
    assert second["shutdown_requested"] is True
    assert thread.join_calls == 1
    assert agent.common.context.shutdown_requested is True
