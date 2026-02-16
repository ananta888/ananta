def test_create_app_skips_background_threads_under_pytest(monkeypatch):
    import agent.ai_agent as ai

    monkeypatch.setenv("PYTEST_CURRENT_TEST", "1")
    called = {"reg": 0, "llm": 0, "mon": 0, "house": 0}

    monkeypatch.setattr(ai, "_start_registration_thread", lambda app: called.__setitem__("reg", called["reg"] + 1))
    monkeypatch.setattr(ai, "_start_llm_check_thread", lambda app: called.__setitem__("llm", called["llm"] + 1))
    monkeypatch.setattr(ai, "_start_monitoring_thread", lambda app: called.__setitem__("mon", called["mon"] + 1))
    monkeypatch.setattr(ai, "_start_housekeeping_thread", lambda app: called.__setitem__("house", called["house"] + 1))

    app = ai.create_app(agent="test-agent")
    app.config.update({"TESTING": True})

    assert called == {"reg": 0, "llm": 0, "mon": 0, "house": 0}


def test_create_app_skips_background_threads_with_explicit_env_switch(monkeypatch):
    import agent.ai_agent as ai

    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("ANANTA_DISABLE_BACKGROUND_THREADS", "1")
    called = {"reg": 0, "llm": 0, "mon": 0, "house": 0}

    monkeypatch.setattr(ai, "_start_registration_thread", lambda app: called.__setitem__("reg", called["reg"] + 1))
    monkeypatch.setattr(ai, "_start_llm_check_thread", lambda app: called.__setitem__("llm", called["llm"] + 1))
    monkeypatch.setattr(ai, "_start_monitoring_thread", lambda app: called.__setitem__("mon", called["mon"] + 1))
    monkeypatch.setattr(ai, "_start_housekeeping_thread", lambda app: called.__setitem__("house", called["house"] + 1))

    app = ai.create_app(agent="test-agent-env")
    app.config.update({"TESTING": False})

    assert called == {"reg": 0, "llm": 0, "mon": 0, "house": 0}
