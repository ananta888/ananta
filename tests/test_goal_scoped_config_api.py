from agent.repository import goal_repo
from agent.services.config_profile_service import get_config_profile_service
from agent.services.goal_config_resolver_service import ALLOWED_GOAL_CONFIG_KEYS


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


# CPR-001: Config-Profiles API — all profiles have required fields, no secret leakage,
# no unknown override keys.
def test_all_profiles_have_required_fields():
    svc = get_config_profile_service()
    for profile in svc.list_profiles():
        assert "id" in profile, f"profile missing 'id': {profile}"
        assert "description" in profile, f"profile missing 'description': {profile}"
        assert "overrides" in profile, f"profile missing 'overrides': {profile}"
        assert isinstance(profile["overrides"], dict)


def test_profiles_api_returns_required_profile_ids(client, admin_auth_header):
    res = client.get("/config/profiles", headers=admin_auth_header)
    assert res.status_code == 200
    ids = {p["id"] for p in res.get_json()["data"]["profiles"]}
    assert {"opencode_preconfigured", "opencode_ollama_local", "ananta_ollama_local", "hermes_free_models_preconfigured"}.issubset(ids)


def test_all_profile_override_keys_are_allowed():
    svc = get_config_profile_service()
    for profile in svc.list_profiles():
        for key in profile["overrides"]:
            assert key in ALLOWED_GOAL_CONFIG_KEYS, (
                f"Profile '{profile['id']}' contains unknown config key '{key}'"
            )


def test_profile_overrides_contain_no_secret_values():
    _SECRET_INDICATORS = ("password", "api_key", "token", "secret", "credential")
    svc = get_config_profile_service()

    def _scan(obj, path=""):
        if isinstance(obj, dict):
            for k, v in obj.items():
                full = f"{path}.{k}" if path else k
                for indicator in _SECRET_INDICATORS:
                    assert indicator not in str(k).lower() or v in (None, ""), (
                        f"Profile overrides may contain secret at '{full}'"
                    )
                _scan(v, full)

    for profile in svc.list_profiles():
        _scan(profile["overrides"], profile["id"])


# TRM-002: End-to-End goal creation matrix for all profiles
import pytest


@pytest.mark.parametrize("profile_id", [
    "opencode_preconfigured",
    "opencode_ollama_local",
    "ananta_ollama_local",
    "ananta_lmstudio_local",
    "opencode_lmstudio_local",
    "hermes_free_models_preconfigured",
])
def test_goal_creation_succeeds_for_all_profiles(profile_id, client, admin_auth_header, monkeypatch):
    _mock_goal_planning_llm(monkeypatch)
    res = client.post(
        "/goals",
        headers=admin_auth_header,
        json={
            "goal": f"Matrix test goal for profile {profile_id}",
            "execution_preferences": {"config_profile": profile_id},
        },
    )
    assert res.status_code == 201, f"profile '{profile_id}' failed: {res.get_json()}"
    goal_id = res.get_json()["data"]["goal"]["id"]
    goal = goal_repo.get_by_id(goal_id)
    prefs = dict(goal.execution_preferences or {})
    assert prefs.get("config_profile") == profile_id
    assert isinstance(prefs.get("config_snapshot"), dict)
    assert prefs["config_snapshot"].get("version") == "goal_config_snapshot.v1"


def test_goal_create_with_explicit_overrides_snapshot_and_effective_config_agree(
    client, admin_auth_header, monkeypatch
):
    _mock_goal_planning_llm(monkeypatch)
    res = client.post(
        "/goals",
        headers=admin_auth_header,
        json={
            "goal": "Snapshot consistency check",
            "execution_preferences": {
                "config_profile": "ananta_ollama_local",
                "config_overrides": {"default_model": "matrix-model"},
            },
        },
    )
    assert res.status_code == 201
    goal_id = res.get_json()["data"]["goal"]["id"]

    effective = client.get(f"/goals/{goal_id}/effective-config", headers=admin_auth_header)
    assert effective.status_code == 200
    data = effective.get_json()["data"]
    # effective-config endpoint and persisted snapshot must agree on model
    cfg = data["config_snapshot"]["config"]
    assert cfg.get("default_model") == "matrix-model"
    assert data.get("goal_config_source") == "snapshot"
    assert isinstance(data.get("config_checksum"), str)


def test_invalid_config_blocks_creation_not_just_planning(client, admin_auth_header, monkeypatch):
    """TRM-002: Unknown override keys must be rejected at create time, before planning runs."""
    planner_calls = []
    original = __import__(
        "agent.routes.tasks.auto_planner", fromlist=["generate_text"]
    ).generate_text

    def _spy(**kwargs):
        planner_calls.append(kwargs)
        return original(**kwargs)

    _mock_goal_planning_llm(monkeypatch)
    monkeypatch.setattr("agent.routes.tasks.auto_planner.generate_text", lambda **kw: planner_calls.append(kw) or "[]")

    res = client.post(
        "/goals",
        headers=admin_auth_header,
        json={
            "goal": "Bad config goal",
            "execution_preferences": {"config_overrides": {"nonexistent_setting": True}},
        },
    )
    assert res.status_code == 400
    assert res.get_json()["message"] == "invalid_goal_config_key"
    assert not planner_calls, "planner must not run when config validation fails"
