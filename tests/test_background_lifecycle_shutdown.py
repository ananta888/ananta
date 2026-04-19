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


def test_background_manager_skip_reloader_does_not_record_partial_start(monkeypatch):
    app = SimpleNamespace(testing=False, extensions={})
    manager = lifecycle.BackgroundServiceManager(app)
    service_calls = []
    signal_calls = []

    monkeypatch.setattr(manager, "_is_testing", lambda: False)
    monkeypatch.setattr(manager, "_should_skip_for_reloader", lambda: True)
    monkeypatch.setattr(manager, "_start_registration", lambda: service_calls.append("registration"))
    monkeypatch.setattr(lifecycle.signal, "signal", lambda sig, handler: signal_calls.append((sig, handler)))

    manager.start_all()

    assert service_calls == []
    assert manager.runtime_state() == {
        "started": [],
        "failed": {},
        "shutdown_requested": False,
        "active_thread_count": 0,
    }
    assert len(signal_calls) == 2


def test_background_manager_shutdown_records_scheduler_stop_failure_and_skips_current_thread(monkeypatch):
    app = SimpleNamespace(testing=False, extensions={})
    live_thread = _FakeThread()
    manager = lifecycle.BackgroundServiceManager(app)

    monkeypatch.setattr(agent.common.context, "shutdown_requested", False)
    monkeypatch.setattr(agent.common.context, "active_threads", [live_thread, lifecycle.threading.current_thread()])
    monkeypatch.setattr(manager, "_stop_scheduler", lambda: (_ for _ in ()).throw(RuntimeError("scheduler down")))

    state = manager.shutdown(join_timeout=0.1)

    assert state["failed"] == {"scheduler_stop": "scheduler down"}
    assert live_thread.join_calls == 1
    assert live_thread.timeout == 0.1
    assert app.extensions["background_services"]["shutdown_requested"] is True
