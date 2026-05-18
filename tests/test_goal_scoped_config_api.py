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


# GSC-002: unknown config_overrides keys rejected at the API boundary
def test_create_goal_rejects_unknown_config_override_key(client, admin_auth_header, monkeypatch):
    _mock_goal_planning_llm(monkeypatch)
    res = client.post(
        "/goals",
        headers=admin_auth_header,
        json={
            "goal": "Test with bad config key",
            "execution_preferences": {
                "config_overrides": {"unknown_future_key": "value", "another_unknown": 42},
            },
        },
    )
    assert res.status_code == 400
    body = res.get_json()
    assert body["message"] == "invalid_goal_config_key"
    assert "unknown_future_key" in body["data"]["unknown_keys"]
    assert "another_unknown" in body["data"]["unknown_keys"]


def test_create_goal_accepts_all_known_config_override_keys(client, admin_auth_header, monkeypatch):
    _mock_goal_planning_llm(monkeypatch)
    res = client.post(
        "/goals",
        headers=admin_auth_header,
        json={
            "goal": "Test with valid config keys",
            "execution_preferences": {
                "config_overrides": {"default_model": "test-model", "default_provider": "ollama"},
            },
        },
    )
    assert res.status_code == 201


# GSC-005: access model for effective-config endpoint uses team-scope, not owner-scope.
# Goals without a team_id are accessible to any authenticated user (by design).
# Unauthenticated requests must be rejected.
def test_effective_config_requires_authentication(client, admin_auth_header, monkeypatch):
    _mock_goal_planning_llm(monkeypatch)
    create = client.post(
        "/goals",
        headers=admin_auth_header,
        json={"goal": "Admin-owned goal for auth test"},
    )
    assert create.status_code == 201
    goal_id = create.get_json()["data"]["goal"]["id"]

    # Unauthenticated access must fail.
    res = client.get(f"/goals/{goal_id}/effective-config")
    assert res.status_code in (401, 403)


def test_effective_config_accessible_to_authenticated_non_owner(client, admin_auth_header, user_auth_header, monkeypatch):
    _mock_goal_planning_llm(monkeypatch)
    create = client.post(
        "/goals",
        headers=admin_auth_header,
        json={"goal": "Admin-owned goal without team restriction"},
    )
    assert create.status_code == 201
    goal_id = create.get_json()["data"]["goal"]["id"]

    # Goals without team_id are team-scope-open: any authenticated user may read the config.
    res = client.get(f"/goals/{goal_id}/effective-config", headers=user_auth_header)
    assert res.status_code == 200
    body = res.get_json()["data"]
    assert "config_snapshot" in body
