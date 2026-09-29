from __future__ import annotations

from types import SimpleNamespace

from agent.services.hub_llm_service import HubLLMService


def test_central_planning_assignment_overrides_legacy_model_choice(app, monkeypatch):
    service = HubLLMService()
    app.config["AGENT_CONFIG"] = {
        "default_provider": "openai",
        "default_model": "legacy-cloud",
        "hub_copilot": {"enabled": True, "strategy_mode": "planning_only"},
    }
    monkeypatch.setattr(service, "_resolve_central_planning_model", lambda **_: SimpleNamespace(
        provider_id="lmstudio",
        model_id="kat-coder",
        base_url="http://mini-pc:1234/v1",
        assignment_source="project:ananta",
        configuration_revision=9,
        profile_id="local-kat-coder",
    ))

    with app.app_context():
        resolved = service.resolve_copilot_config(task_kind="coding")

    assert resolved["effective"] == {
        "provider": "lmstudio",
        "model": "kat-coder",
        "base_url": "http://mini-pc:1234/v1",
        "temperature": None,
    }
    assert resolved["source"]["provider"] == "model_routing:project:ananta"
    assert resolved["source"]["base_url"] == "model_profile:local-kat-coder"
    assert resolved["model_routing"] == {
        "configuration_revision": 9,
        "assignment_source": "project:ananta",
        "profile_id": "local-kat-coder",
    }
