import pytest
import time
import json
import os
from agent.ai_agent import create_app
from agent.routes.auth import login_attempts

@pytest.fixture
def client():
    login_attempts.clear()
    app = create_app()
    app.config["TESTING"] = True
    app.config["DATA_DIR"] = "data_test"
    os.makedirs("data_test", exist_ok=True)
    with app.test_client() as client:
        yield client
    # Cleanup
    if os.path.exists("data_test/users.json"):
        os.remove("data_test/users.json")
    if os.path.exists("data_test/refresh_tokens.json"):
        os.remove("data_test/refresh_tokens.json")

def test_login_rate_limiting(client):
    login_attempts.clear()
    # 5 Versuche sind erlaubt, der 6. sollte fehlschlagen (429)
    for i in range(5):
        response = client.post("/login", json={"username": "admin", "password": "wrong-password"})
        assert response.status_code == 401
    
    response = client.post("/login", json={"username": "admin", "password": "wrong-password"})
    assert response.status_code == 429
    assert b"Too many login attempts" in response.data

def test_admin_user_management(client):
    # 1. Login als Admin (Default Passwort ist "admin" laut agent/routes/auth.py)
    response = client.post("/login", json={"username": "admin", "password": "admin"})
    assert response.status_code == 200
    token = response.json["token"]
    headers = {"Authorization": f"Bearer {token}"}
    
    # 2. User Liste abrufen
    response = client.get("/users", headers=headers)
    assert response.status_code == 200
    assert len(response.json) >= 1
    
    # 3. Neuen User anlegen
    response = client.post("/users", headers=headers, json={
        "username": "testuser",
        "password": "testpassword",
        "role": "user"
    })
    assert response.status_code == 200
    
    # 4. Prüfen ob User existiert
    response = client.get("/users", headers=headers)
    assert any(u["username"] == "testuser" for u in response.json)
    
    # 5. Passwort resetten
    response = client.post("/users/testuser/reset-password", headers=headers, json={
        "new_password": "newpassword"
    })
    assert response.status_code == 200
    
    # 6. Login mit neuem Passwort
    response = client.post("/login", json={"username": "testuser", "password": "newpassword"})
    assert response.status_code == 200
    
    # 7. Rolle ändern
    response = client.put("/users/testuser/role", headers=headers, json={"role": "admin"})
    assert response.status_code == 200
    
    # 8. User löschen
    response = client.delete("/users/testuser", headers=headers)
    assert response.status_code == 200
    
    # 9. Prüfen ob User gelöscht ist
    response = client.get("/users", headers=headers)
    assert not any(u["username"] == "testuser" for u in response.json)
