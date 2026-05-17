from agent.repository import goal_repo
from agent.services.goal_config_runtime_service import get_goal_config_runtime_service


def _mock_goal_planning_llm(monkeypatch):
    monkeypatch.setattr(
        "agent.routes.tasks.auto_planner.generate_text",
        lambda **kwargs: '[{"title":"Plan","description":"Do work","priority":"Medium"}]',
    )
    monkeypatch.setattr("agent.services.planning_strategies.try_load_repo_context", lambda goal: None)


def test_planner_path_records_snapshot_source(client, admin_auth_header, monkeypatch):
    _mock_goal_planning_llm(monkeypatch)
    res = client.post(
        "/goals",
        headers=admin_auth_header,
        json={"goal": "Planner scoped test", "execution_preferences": {"config_profile": "ananta_ollama_local"}},
    )
    assert res.status_code == 201
    goal_id = res.get_json()["data"]["goal"]["id"]
    effective = client.get(f"/goals/{goal_id}/effective-config", headers=admin_auth_header)
    assert effective.status_code == 200
    assert effective.get_json()["data"].get("goal_config_source") == "snapshot"


def test_global_config_change_does_not_mutate_goal_snapshot(client, admin_auth_header, monkeypatch):
    _mock_goal_planning_llm(monkeypatch)
    create = client.post(
        "/goals",
        headers=admin_auth_header,
        json={"goal": "Snapshot stability", "execution_preferences": {"config_profile": "ananta_ollama_local"}},
    )
    goal_id = create.get_json()["data"]["goal"]["id"]
    before = client.get(f"/goals/{goal_id}/effective-config", headers=admin_auth_header).get_json()["data"]
    before_checksum = before.get("config_checksum")

    patch = client.post("/config", headers=admin_auth_header, json={"default_provider": "lmstudio", "default_model": "qwen2.5-coder:7b"})
    assert patch.status_code == 200

    after = client.get(f"/goals/{goal_id}/effective-config", headers=admin_auth_header).get_json()["data"]
    assert after.get("config_checksum") == before_checksum
    assert after.get("goal_config_source") == "snapshot"


def test_runtime_service_uses_snapshot_for_goal_tasks(client, admin_auth_header, monkeypatch):
    _mock_goal_planning_llm(monkeypatch)
    create = client.post(
        "/goals",
        headers=admin_auth_header,
        json={"goal": "Runtime scoped cfg", "execution_preferences": {"config_profile": "ananta_ollama_local"}},
    )
    goal_id = create.get_json()["data"]["goal"]["id"]
    goal = goal_repo.get_by_id(goal_id)
    prefs = dict(goal.execution_preferences or {})
    expected_checksum = prefs.get("config_snapshot_checksum")

    resolved = get_goal_config_runtime_service().get_effective_config(goal_id=goal_id)
    assert resolved.source == "snapshot"
    assert resolved.checksum == expected_checksum
    assert resolved.config.get("default_provider") == "ollama"
