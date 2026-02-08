import pytest
import json
import os

# Environment setzen bevor imports
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["INITIAL_ADMIN_USER"] = "admin"
os.environ["INITIAL_ADMIN_PASSWORD"] = "admin"

from agent.ai_agent import create_app

@pytest.fixture
def client():
    app = create_app(agent="test-agent")
    app.config.update({"TESTING": True})
    return app.test_client()

def get_auth_header(client):
    resp = client.post("/auth/login", json={"username": "admin", "password": "admin"})
    print("Login Response:", resp.get_json())
    data = resp.get_json()
    if data and "data" in data and "access_token" in data["data"]:
        token = data["data"]["access_token"]
        return {"Authorization": f"Bearer {token}"}
    
    # Fallback/Retry für /login (manche Blueprints haben unterschiedliche Prefixe)
    resp = client.post("/login", json={"username": "admin", "password": "admin"})
    print("Login Retry Response:", resp.get_json())
    data = resp.get_json()
    token = data["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}

def test_config_wrapping_bug(client):
    headers = get_auth_header(client)
    
    # 1. Normale Config setzen
    config_data = {"llm_config": {"provider": "lmstudio", "model": "test-model"}}
    resp = client.post("/config", json=config_data, headers=headers)
    assert resp.status_code == 200
    
    # 2. Config abrufen - sollte normal sein
    resp = client.get("/config", headers=headers)
    data = resp.get_json()
    print("\nErster Abruf:", json.dumps(data, indent=2))
    assert data["status"] == "success"
    assert "llm_config" in data["data"]
    
    # 3. Jetzt simulieren wir einen Frontend-Fehler oder redundantes Einpacken
    # Wir senden das zurück, was wir bekommen haben (inklusive status/data wrapper)
    redundant_data = data # Das ist {"status": "success", "data": {...}}
    resp = client.post("/config", json=redundant_data, headers=headers)
    assert resp.status_code == 200
    
    # 4. Erneut abrufen
    resp = client.get("/config", headers=headers)
    data2 = resp.get_json()
    print("\nZweiter Abruf (nach redundantem Post):", json.dumps(data2, indent=2))
    
    # Der Bug: data2["data"]["llm_config"] könnte jetzt verschachtelt sein
    llm_cfg = data2["data"].get("llm_config")
    if isinstance(llm_cfg, dict) and "data" in llm_cfg:
        print("\nBUG BESTÄTIGT: llm_config ist verschachtelt!")
        print(json.dumps(llm_cfg, indent=2))
    else:
        print("\nKeine Verschachtelung in llm_config gefunden (nach 1. Redundanz)")

    # 5. Noch eine Ebene tiefer
    client.post("/config", json=data2, headers=headers)
    resp = client.get("/config", headers=headers)
    data3 = resp.get_json()
    print("\nDritter Abruf (nach 2. Redundanz):", json.dumps(data3, indent=2))
    
    llm_cfg = data3["data"].get("llm_config")
    if isinstance(llm_cfg, dict) and "data" in llm_cfg:
        print("\nBUG BESTÄTIGT: llm_config ist MEHRFACH verschachtelt!")
    
if __name__ == "__main__":
    # Manuelle Ausführung falls gewünscht
    app = create_app(agent="test-agent")
    client_inst = app.test_client()
    test_config_wrapping_bug(client_inst)
