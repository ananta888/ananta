import pytest
import json

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
