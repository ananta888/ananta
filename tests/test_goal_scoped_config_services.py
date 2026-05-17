from agent.repository import goal_repo
from agent.services.config_profile_service import get_config_profile_service
from agent.services.goal_config_resolver_service import get_goal_config_resolver_service
from agent.services.goal_config_runtime_service import get_goal_config_runtime_service


def test_config_profile_catalog_contains_required_profiles():
    profiles = {item["id"] for item in get_config_profile_service().list_profiles()}
    assert {"opencode_preconfigured", "opencode_ollama_local", "ananta_ollama_local"}.issubset(profiles)


def test_goal_config_resolver_redacts_secret_fields():
    resolver = get_goal_config_resolver_service()
    result = resolver.resolve(
        system_config={"llm_config": {"api_key": "secret", "base_url": "http://localhost"}, "default_provider": "ollama"},
        profile_id=None,
        goal_overrides={"default_model": "ananta-default:latest"},
    )
    cfg = result.config_snapshot["config"]
    assert cfg["llm_config"]["api_key"] == "***REDACTED***"
    assert result.redaction_summary["redacted_fields"] >= 1
    assert isinstance(result.checksum, str) and len(result.checksum) == 64


def test_create_goal_persists_goal_scoped_snapshot(client, admin_auth_header, monkeypatch):
    monkeypatch.setattr(
        "agent.routes.tasks.auto_planner.generate_text",
        lambda **kwargs: '[{"title":"Plan","description":"Do work","priority":"Medium"}]',
    )
    monkeypatch.setattr("agent.services.planning_strategies.try_load_repo_context", lambda goal: None)
    res = client.post(
        "/goals",
        headers=admin_auth_header,
        json={
            "goal": "Create fibonacci backend",
            "execution_preferences": {
                "config_profile": "ananta_ollama_local",
                "config_overrides": {"default_model": "ananta-default:latest"},
            },
        },
    )
    assert res.status_code == 201
    goal_id = res.get_json()["data"]["goal"]["id"]
    persisted = goal_repo.get_by_id(goal_id)
    prefs = dict(persisted.execution_preferences or {})
    snapshot = dict(prefs.get("config_snapshot") or {})
    assert prefs.get("config_profile") == "ananta_ollama_local"
    assert snapshot.get("version") == "goal_config_snapshot.v1"
    assert isinstance(prefs.get("config_snapshot_checksum"), str)

    effective = get_goal_config_runtime_service().get_effective_config(goal_id=goal_id)
    assert effective.source == "snapshot"
    assert effective.config.get("default_provider") == "ollama"


def test_goal_create_rejects_unknown_config_profile(client, admin_auth_header):
    res = client.post(
        "/goals",
        headers=admin_auth_header,
        json={
            "goal": "Invalid profile goal",
            "execution_preferences": {"config_profile": "does-not-exist"},
        },
    )
    assert res.status_code == 400
    assert res.get_json()["message"] == "unknown_config_profile"


def test_config_profiles_endpoint(client, admin_auth_header):
    res = client.get("/config/profiles", headers=admin_auth_header)
    assert res.status_code == 200
    payload = res.get_json()["data"]
    assert isinstance(payload.get("profiles"), list)
    assert any(item.get("id") == "ananta_ollama_local" for item in payload["profiles"])
