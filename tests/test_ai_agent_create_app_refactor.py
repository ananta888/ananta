import agent.ai_agent as ai_agent
import agent.lifecycle as lifecycle
from agent.services.background import llm_check


def test_should_skip_threads_for_reloader(monkeypatch):
    monkeypatch.setenv("FLASK_DEBUG", "1")
    monkeypatch.delenv("WERKZEUG_RUN_MAIN", raising=False)
    assert ai_agent._should_skip_threads_for_reloader() is True

    monkeypatch.setenv("WERKZEUG_RUN_MAIN", "true")
    assert ai_agent._should_skip_threads_for_reloader() is False


def test_start_background_services_respects_disabled(monkeypatch):
    calls = {"registration": 0, "llm": 0, "monitoring": 0, "housekeeping": 0, "scheduler": 0}

    monkeypatch.setattr(lifecycle.BackgroundServiceManager, "_is_testing", lambda self: True)
    monkeypatch.setattr(lifecycle.BackgroundServiceManager, "_should_skip_for_reloader", lambda self: False)
    monkeypatch.setattr(lifecycle.BackgroundServiceManager, "_start_registration", lambda self: calls.__setitem__("registration", 1))
    monkeypatch.setattr(lifecycle.BackgroundServiceManager, "_start_llm_monitoring", lambda self: calls.__setitem__("llm", 1))
    monkeypatch.setattr(lifecycle.BackgroundServiceManager, "_start_monitoring", lambda self: calls.__setitem__("monitoring", 1))
    monkeypatch.setattr(lifecycle.BackgroundServiceManager, "_start_housekeeping", lambda self: calls.__setitem__("housekeeping", 1))
    monkeypatch.setattr(lifecycle.BackgroundServiceManager, "_start_scheduler", lambda self: calls.__setitem__("scheduler", 1))

    lifecycle.BackgroundServiceManager(object()).start_all()
    assert calls == {"registration": 0, "llm": 0, "monitoring": 0, "housekeeping": 0, "scheduler": 0}


def test_start_background_services_runs_expected_calls(monkeypatch):
    calls = {"registration": 0, "llm": 0, "monitoring": 0, "housekeeping": 0, "scheduler": 0}

    monkeypatch.setattr(lifecycle.BackgroundServiceManager, "_is_testing", lambda self: False)
    monkeypatch.setattr(lifecycle.BackgroundServiceManager, "_should_skip_for_reloader", lambda self: False)
    monkeypatch.setattr(lifecycle.BackgroundServiceManager, "_start_registration", lambda self: calls.__setitem__("registration", 1))
    monkeypatch.setattr(lifecycle.BackgroundServiceManager, "_start_llm_monitoring", lambda self: calls.__setitem__("llm", 1))
    monkeypatch.setattr(lifecycle.BackgroundServiceManager, "_start_monitoring", lambda self: calls.__setitem__("monitoring", 1))
    monkeypatch.setattr(lifecycle.BackgroundServiceManager, "_start_housekeeping", lambda self: calls.__setitem__("housekeeping", 1))
    monkeypatch.setattr(lifecycle.BackgroundServiceManager, "_start_scheduler", lambda self: calls.__setitem__("scheduler", 1))
    monkeypatch.setattr(lifecycle.settings, "disable_llm_check", False)

    lifecycle.BackgroundServiceManager(object()).start_all()
    assert calls == {"registration": 1, "llm": 1, "monitoring": 1, "housekeeping": 1, "scheduler": 1}


def test_get_llm_target_prefers_runtime_default_provider_from_app_config(app):
    app.config["AGENT_CONFIG"] = {
        **(app.config.get("AGENT_CONFIG") or {}),
        "default_provider": "lmstudio",
    }
    app.config["PROVIDER_URLS"] = {
        **(app.config.get("PROVIDER_URLS") or {}),
        "lmstudio": "http://runtime-lmstudio:1234/v1",
    }

    with app.app_context():
        target = llm_check._get_llm_target(app)

    assert target == ("lmstudio", "http://runtime-lmstudio:1234/v1")
