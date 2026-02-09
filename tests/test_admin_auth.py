import pytest
import time
import json
import os
from agent.ai_agent import create_app
from agent.repository import login_attempt_repo
from agent.database import engine
from sqlmodel import Session

@pytest.fixture
def client():
    # Wir müssen erst create_app rufen, damit init_db() die Tabellen anlegt
    # bevor wir das Repository nutzen.
    app = create_app()
    app.config["TESTING"] = True
    app.config["DATA_DIR"] = "data_test"
    os.makedirs("data_test", exist_ok=True)
    
    login_attempt_repo.clear_all()
    # Auch User zurücksetzen für saubere Tests
    from agent.repository import user_repo
    with Session(engine) as session:
        from sqlmodel import delete
        from agent.db_models import UserDB
        session.exec(delete(UserDB))
        session.commit()
    
    # Standard Admin anlegen
    from werkzeug.security import generate_password_hash
    from agent.db_models import UserDB
    user_repo.save(UserDB(username="admin", password_hash=generate_password_hash("admin"), role="admin"))
    
    with app.test_client() as client:
        yield client
    # Cleanup
    if os.path.exists("data_test/users.json"):
        os.remove("data_test/users.json")
    if os.path.exists("data_test/refresh_tokens.json"):
        os.remove("data_test/refresh_tokens.json")

def test_login_rate_limiting(client):
    login_attempt_repo.clear_all()
    # 10 Versuche sind erlaubt (IP-basiert), aber Account-Lockout greift nach 5
    for i in range(5):
        response = client.post("/login", json={"username": "admin", "password": "wrong-password"})
        assert response.status_code == 401
    
    for i in range(5, 10):
        response = client.post("/login", json={"username": "admin", "password": "wrong-password"})
        assert response.status_code == 403
    
    response = client.post("/login", json={"username": "admin", "password": "wrong-password"})
    assert response.status_code == 429
    assert b"Too many login attempts" in response.data

def test_admin_user_management(client):
    # 1. Login als Admin (Default Passwort ist "admin" laut agent/routes/auth.py)
    response = client.post("/login", json={"username": "admin", "password": "admin"})
    assert response.status_code == 200
    token = response.json["data"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    
    # 2. User Liste abrufen
    response = client.get("/users", headers=headers)
    assert response.status_code == 200
    assert len(response.json["data"]) >= 1
    
    # 3. Neuen User anlegen
    response = client.post("/users", headers=headers, json={
        "username": "testuser",
        "password": "TestPassword123!",
        "role": "user"
    })
    assert response.status_code == 200
    
    # 4. Prüfen ob User existiert
    response = client.get("/users", headers=headers)
    assert any(u["username"] == "testuser" for u in response.json["data"])
    
    # 5. Passwort resetten
    response = client.post("/users/testuser/reset-password", headers=headers, json={
        "new_password": "NewTestPassword123!"
    })
    assert response.status_code == 200
    
    # 6. Login mit neuem Passwort
    response = client.post("/login", json={"username": "testuser", "password": "NewTestPassword123!"})
    assert response.status_code == 200
    
    # 7. Rolle ändern
    response = client.put("/users/testuser/role", headers=headers, json={"role": "admin"})
    assert response.status_code == 200
    
    # 8. User löschen
    response = client.delete("/users/testuser", headers=headers)
    assert response.status_code == 200
    
    # 9. Prüfen ob User gelöscht ist
    response = client.get("/users", headers=headers)
    assert not any(u["username"] == "testuser" for u in response.json["data"])

def test_account_lockout(client):
    # User anlegen (direkt in DB/Repo da wir hier im Test-Setup sind)
    from agent.db_models import UserDB
    from agent.repository import user_repo
    from werkzeug.security import generate_password_hash
    
    username = "lockout_test"
    password = "password123!"
    user_repo.save(UserDB(
        username=username, 
        password_hash=generate_password_hash(password),
        role="user"
    ))
    
    # 5 Fehlversuche
    for i in range(5):
        response = client.post("/login", json={"username": username, "password": "wrong-password"})
        assert response.status_code == 401
    
    # Der 6. Versuch sollte gesperrt sein (403 Forbidden)
    response = client.post("/login", json={"username": username, "password": "wrong-password"})
    assert response.status_code == 403
    assert b"Account is locked" in response.data
    
    # Login mit korrektem Passwort sollte auch gesperrt sein
    response = client.post("/login", json={"username": username, "password": password})
    assert response.status_code == 403
