import agent.ai_agent as ai_agent


def test_should_skip_threads_for_reloader(monkeypatch):
    monkeypatch.setenv("FLASK_DEBUG", "1")
    monkeypatch.delenv("WERKZEUG_RUN_MAIN", raising=False)
    assert ai_agent._should_skip_threads_for_reloader() is True

    monkeypatch.setenv("WERKZEUG_RUN_MAIN", "true")
    assert ai_agent._should_skip_threads_for_reloader() is False


def test_start_background_services_respects_disabled(monkeypatch):
    calls = {"registration": 0, "llm": 0, "monitoring": 0, "housekeeping": 0, "scheduler": 0}

    class DummyScheduler:
        def start(self):
            calls["scheduler"] += 1

    monkeypatch.setattr(ai_agent, "_should_skip_threads_for_reloader", lambda: False)
    monkeypatch.setattr(ai_agent, "_background_threads_disabled", lambda app: True)
    monkeypatch.setattr(ai_agent, "_start_registration_thread", lambda app: calls.__setitem__("registration", 1))
    monkeypatch.setattr(ai_agent, "_start_llm_check_thread", lambda app: calls.__setitem__("llm", 1))
    monkeypatch.setattr(ai_agent, "_start_monitoring_thread", lambda app: calls.__setitem__("monitoring", 1))
    monkeypatch.setattr(ai_agent, "_start_housekeeping_thread", lambda app: calls.__setitem__("housekeeping", 1))
    monkeypatch.setattr("agent.scheduler.get_scheduler", lambda: DummyScheduler())

    ai_agent._start_background_services(object())
    assert calls == {"registration": 0, "llm": 0, "monitoring": 0, "housekeeping": 0, "scheduler": 0}


def test_start_background_services_runs_expected_calls(monkeypatch):
    calls = {"registration": 0, "llm": 0, "monitoring": 0, "housekeeping": 0, "scheduler": 0}

    class DummyScheduler:
        def start(self):
            calls["scheduler"] += 1

    monkeypatch.setattr(ai_agent, "_should_skip_threads_for_reloader", lambda: False)
    monkeypatch.setattr(ai_agent, "_background_threads_disabled", lambda app: False)
    monkeypatch.setattr(ai_agent, "_start_registration_thread", lambda app: calls.__setitem__("registration", 1))
    monkeypatch.setattr(ai_agent, "_start_llm_check_thread", lambda app: calls.__setitem__("llm", 1))
    monkeypatch.setattr(ai_agent, "_start_monitoring_thread", lambda app: calls.__setitem__("monitoring", 1))
    monkeypatch.setattr(ai_agent, "_start_housekeeping_thread", lambda app: calls.__setitem__("housekeeping", 1))
    monkeypatch.setattr("agent.scheduler.get_scheduler", lambda: DummyScheduler())
    monkeypatch.setattr(ai_agent.settings, "disable_llm_check", False)

    ai_agent._start_background_services(object())
    assert calls == {"registration": 1, "llm": 1, "monitoring": 1, "housekeeping": 1, "scheduler": 1}
