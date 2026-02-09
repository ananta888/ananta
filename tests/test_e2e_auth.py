import pytest
import time
from agent.db_models import UserDB
from agent.repository import user_repo

def test_full_auth_flow(client, db_session):
    """
    E2E Test für den kompletten Authentifizierungs-Flow:
    1. User Erstellung (via Admin/DB)
    2. Login
    3. Passwortänderung
    4. Login mit neuem Passwort
    """
    username = "testuser_e2e"
    password = "SecurePassword123!"
    new_password = "EvenMoreSecure456!"
    
    # 1. User erstellen (simuliert Admin-Action)
    from werkzeug.security import generate_password_hash
    user = UserDB(
        username=username,
        password_hash=generate_password_hash(password),
        role="user"
    )
    user_repo.save(user)
    
    # 2. Login
    login_data = {"username": username, "password": password}
    response = client.post("/login", json=login_data)
    assert response.status_code == 200
    assert "access_token" in response.json["data"]
    access_token = response.json["data"]["access_token"]
    
    # 3. Passwort ändern
    headers = {"Authorization": f"Bearer {access_token}"}
    change_data = {
        "old_password": password,
        "new_password": new_password
    }
    response = client.post("/change-password", json=change_data, headers=headers)
    assert response.status_code == 200
    assert response.json["data"]["status"] == "password_changed"
    
    # 4. Login mit altem Passwort sollte fehlschlagen
    response = client.post("/login", json=login_data)
    assert response.status_code == 401
    
    # 5. Login mit neuem Passwort
    login_data["password"] = new_password
    response = client.post("/login", json=login_data)
    assert response.status_code == 200
    assert "access_token" in response.json["data"]

def test_admin_user_management_flow(client, db_session):
    """
    Testet den Flow für Admin-Benutzerverwaltung:
    1. Admin Login
    2. User erstellen via API
    3. User löschen via API
    """
    # Admin-User existiert bereits durch conftest oder init_db
    # Wir erstellen uns einen Admin-Token
    from agent.config import settings
    import jwt
    admin_payload = {"sub": "admin", "role": "admin", "exp": time.time() + 3600}
    admin_token = jwt.encode(admin_payload, settings.secret_key, algorithm="HS256")
    headers = {"Authorization": f"Bearer {admin_token}"}
    
    # 2. Neuen User via API erstellen
    new_user_data = {
        "username": "api_user",
        "password": "Password123!@#",
        "role": "user"
    }
    response = client.post("/users", json=new_user_data, headers=headers)
    assert response.status_code == 200
    assert response.json["data"]["status"] == "user_created"
    
    # 3. User auflisten
    response = client.get("/users", headers=headers)
    assert response.status_code == 200
    usernames = [u["username"] for u in response.json["data"]]
    assert "api_user" in usernames
    
    # 4. User löschen
    response = client.delete("/users/api_user", headers=headers)
    assert response.status_code == 200
    assert response.json["data"]["status"] == "user_deleted"
    
    # 5. Verifizieren, dass User weg ist
    response = client.get("/users", headers=headers)
    usernames = [u["username"] for u in response.json["data"]]
    assert "api_user" not in usernames
