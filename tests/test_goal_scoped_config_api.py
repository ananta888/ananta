from agent.repository import goal_repo


def _mock_goal_planning_llm(monkeypatch):
    monkeypatch.setattr(
        "agent.routes.tasks.auto_planner.generate_text",
        lambda **kwargs: '[{"title":"Plan","description":"Do work","priority":"Medium"}]',
    )
    monkeypatch.setattr("agent.services.planning_strategies.try_load_repo_context", lambda goal: None)


def test_goal_create_with_profile_persists_snapshot(client, admin_auth_header, monkeypatch):
    _mock_goal_planning_llm(monkeypatch)
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
    goal = goal_repo.get_by_id(goal_id)
    prefs = dict(goal.execution_preferences or {})
    assert isinstance(prefs.get("config_snapshot"), dict)
    assert isinstance(prefs.get("config_snapshot_checksum"), str)


def test_goal_effective_config_endpoint_returns_redacted_snapshot(client, admin_auth_header, monkeypatch):
    _mock_goal_planning_llm(monkeypatch)
    create = client.post(
        "/goals",
        headers=admin_auth_header,
        json={
            "goal": "Create service",
            "execution_preferences": {
                "config_profile": "ananta_ollama_local",
                "config_overrides": {"llm_config": {"api_key": "secret"}},
            },
        },
    )
    goal_id = create.get_json()["data"]["goal"]["id"]
    res = client.get(f"/goals/{goal_id}/effective-config", headers=admin_auth_header)
    assert res.status_code == 200
    data = res.get_json()["data"]
    assert data["goal_config_source"] == "snapshot"
    assert isinstance(data.get("config_checksum"), str)
    assert str(data["config_snapshot"]["config"]["llm_config"]["api_key"]).startswith("***REDACTED")


def test_legacy_goal_payload_without_config_profile_still_works(client, admin_auth_header, monkeypatch):
    _mock_goal_planning_llm(monkeypatch)
    res = client.post("/goals", headers=admin_auth_header, json={"goal": "Legacy payload"})
    assert res.status_code == 201
