import pytest
import json
from unittest.mock import patch

@pytest.fixture
def admin_token(client):
    # Admin User anlegen
    from agent.repository import user_repo
    from werkzeug.security import generate_password_hash
    from agent.db_models import UserDB
    
    username = "api_test_admin"
    password = "password123"
    user_repo.save(UserDB(
        username=username,
        password_hash=generate_password_hash(password),
        role="admin"
    ))
    
    # Login
    response = client.post("/login", json={
        "username": username,
        "password": password
    })
    return response.json["data"]["access_token"]

def test_get_config(client, admin_token):
    response = client.get("/config", headers={
        "Authorization": f"Bearer {admin_token}"
    })
    assert response.status_code == 200
    assert "data" in response.json

def test_set_config_unwrapping(client, admin_token):
    # Wir senden eine verschachtelte Konfiguration (simulierter Bug im Frontend/API)
    nested_config = {
        "llm_config": {
            "status": "success",
            "data": {
                "provider": "openai",
                "model": "gpt-4o"
            }
        },
        "default_provider": "openai"
    }
    
    response = client.post("/config", json=nested_config, headers={
        "Authorization": f"Bearer {admin_token}"
    })
    assert response.status_code == 200
    
    # Jetzt prüfen ob es korrekt in der DB und im app.config gelandet ist
    # Wir rufen GET /config auf
    get_res = client.get("/config", headers={
        "Authorization": f"Bearer {admin_token}"
    })
    config_data = get_res.json["data"]
    
    assert config_data["llm_config"]["provider"] == "openai"
    assert config_data["llm_config"]["model"] == "gpt-4o"
    assert config_data["default_provider"] == "openai"
    # Sicherstellen, dass "status" und "data" Schlüssel weg sind
    assert "status" not in config_data["llm_config"]

def test_set_config_forbidden_for_user(client):
    # Normalen User anlegen
    from agent.repository import user_repo
    from werkzeug.security import generate_password_hash
    from agent.db_models import UserDB
    
    username = "normal_user"
    password = "password123"
    user_repo.save(UserDB(
        username=username,
        password_hash=generate_password_hash(password),
        role="user"
    ))
    
    # Login
    login_res = client.post("/login", json={
        "username": username,
        "password": password
    })
    token = login_res.json["data"]["access_token"]
    
    # POST versuchen
    response = client.post("/config", json={"foo": "bar"}, headers={
        "Authorization": f"Bearer {token}"
    })
    assert response.status_code == 403

def test_llmstudio_mode_persists(client, admin_token):
    headers = {
        "Authorization": f"Bearer {admin_token}"
    }
    payload = {"llm_config": {"provider": "lmstudio", "lmstudio_api_mode": "completions"}}
    response = client.post("/config", json=payload, headers=headers)
    assert response.status_code == 200

    get_response = client.get("/config", headers=headers)
    assert get_response.status_code == 200
    cfg = get_response.json["data"]
    assert cfg["llm_config"]["lmstudio_api_mode"] == "completions"

def test_llmstudio_mode_not_dropped_by_partial_llm_update(client, admin_token):
    headers = {
        "Authorization": f"Bearer {admin_token}"
    }
    first = {"llm_config": {"provider": "lmstudio", "model": "m1", "lmstudio_api_mode": "completions"}}
    response = client.post("/config", json=first, headers=headers)
    assert response.status_code == 200

    # Simulate frontend update payloads that omit mode while changing other fields.
    second = {"llm_config": {"provider": "lmstudio", "model": "m2"}}
    response = client.post("/config", json=second, headers=headers)
    assert response.status_code == 200

    get_response = client.get("/config", headers=headers)
    assert get_response.status_code == 200
    cfg = get_response.json["data"]
    assert cfg["llm_config"]["model"] == "m2"
    assert cfg["llm_config"]["lmstudio_api_mode"] == "completions"


def test_list_providers_uses_dynamic_lmstudio_models(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    client.post(
        "/config",
        json={"default_provider": "lmstudio", "default_model": "model-a"},
        headers=headers,
    )
    with patch("agent.routes.config._list_lmstudio_candidates") as mock_candidates:
        mock_candidates.return_value = [
            {"id": "model-a", "context_length": 8192},
            {"id": "model-b", "context_length": 4096},
        ]
        res = client.get("/providers", headers=headers)

    assert res.status_code == 200
    items = res.json["data"]
    ids = [i["id"] for i in items]
    assert "lmstudio:model-a" in ids
    assert "lmstudio:model-b" in ids
    selected = next((i for i in items if i["id"] == "lmstudio:model-a"), None)
    assert selected is not None and selected["selected"] is True


def test_provider_catalog_contains_dynamic_lmstudio_block(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    client.post(
        "/config",
        json={"default_provider": "lmstudio", "default_model": "model-x"},
        headers=headers,
    )
    with patch("agent.routes.config._list_lmstudio_candidates") as mock_candidates:
        mock_candidates.return_value = [{"id": "model-x", "context_length": 32768}]
        res = client.get("/providers/catalog?force_refresh=1", headers=headers)

    assert res.status_code == 200
    data = res.json["data"]
    assert data["default_provider"] == "lmstudio"
    lmstudio = next((p for p in data["providers"] if p["provider"] == "lmstudio"), None)
    assert lmstudio is not None
    assert lmstudio["available"] is True
    assert any(m["id"] == "model-x" and m["selected"] is True for m in (lmstudio.get("models") or []))


def test_provider_catalog_handles_lmstudio_candidate_errors(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    client.post(
        "/config",
        json={"default_provider": "lmstudio", "default_model": "fallback-model"},
        headers=headers,
    )
    with patch("agent.routes.config._list_lmstudio_candidates", side_effect=RuntimeError("offline")):
        res = client.get("/providers/catalog?force_refresh=1", headers=headers)

    assert res.status_code == 200
    data = res.json["data"]
    lmstudio = next((p for p in data["providers"] if p["provider"] == "lmstudio"), None)
    assert lmstudio is not None
    assert lmstudio["available"] is False
    assert lmstudio["model_count"] == 0
    assert lmstudio["models"] == []


def test_provider_catalog_uses_cache_and_force_refresh(client, admin_token):
    from agent.routes import config as config_routes

    headers = {"Authorization": f"Bearer {admin_token}"}
    client.post(
        "/config",
        json={"default_provider": "lmstudio", "default_model": "model-cache"},
        headers=headers,
    )
    config_routes._LMSTUDIO_CATALOG_CACHE.clear()
    with patch("agent.routes.config._list_lmstudio_candidates") as mock_candidates:
        mock_candidates.return_value = [{"id": "model-cache", "context_length": 8192}]

        r1 = client.get("/providers/catalog?cache_ttl_seconds=60", headers=headers)
        r2 = client.get("/providers/catalog?cache_ttl_seconds=60", headers=headers)
        r3 = client.get("/providers/catalog?cache_ttl_seconds=60&force_refresh=1", headers=headers)

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r3.status_code == 200
    assert mock_candidates.call_count == 2


def test_provider_catalog_passes_custom_lmstudio_timeout(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    client.post(
        "/config",
        json={"default_provider": "lmstudio", "default_model": "model-timeout"},
        headers=headers,
    )
    seen = {"timeout": None}

    def _fake_candidates(_url, timeout=0):
        seen["timeout"] = timeout
        return [{"id": "model-timeout", "context_length": 4096}]

    with patch("agent.routes.config._list_lmstudio_candidates", side_effect=_fake_candidates):
        res = client.get("/providers/catalog?force_refresh=1&lmstudio_timeout_seconds=9", headers=headers)

    assert res.status_code == 200
    assert seen["timeout"] == 9


def test_provider_catalog_cache_has_bounded_size(client, admin_token):
    from agent.routes import config as config_routes

    headers = {"Authorization": f"Bearer {admin_token}"}
    client.post(
        "/config",
        json={"default_provider": "lmstudio", "default_model": "model-bound"},
        headers=headers,
    )

    config_routes._LMSTUDIO_CATALOG_CACHE.clear()
    with patch("agent.routes.config._list_lmstudio_candidates", return_value=[]):
        for i in range(config_routes._LMSTUDIO_CATALOG_CACHE_MAX_ENTRIES + 12):
            client.post(
                "/config",
                json={"lmstudio_url": f"http://127.0.0.1:{1200 + i}/v1"},
                headers=headers,
            )
            res = client.get("/providers/catalog?cache_ttl_seconds=60&force_refresh=1", headers=headers)
            assert res.status_code == 200

    assert len(config_routes._LMSTUDIO_CATALOG_CACHE) <= config_routes._LMSTUDIO_CATALOG_CACHE_MAX_ENTRIES
