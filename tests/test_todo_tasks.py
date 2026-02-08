import pytest
import time
import jwt
from agent.ai_agent import create_app
from agent.repository import user_repo, login_attempt_repo, refresh_token_repo
from agent.database import engine
from sqlmodel import Session, delete
from agent.db_models import UserDB, RefreshTokenDB
from werkzeug.security import generate_password_hash
from agent.common.mfa import generate_mfa_secret, encrypt_secret, decrypt_secret, verify_totp
import pyotp

@pytest.fixture
def client():
    app = create_app()
    app.config["TESTING"] = True
    app.config["DATA_DIR"] = "data_test"
    
    with Session(engine) as session:
        session.exec(delete(RefreshTokenDB))
        session.exec(delete(UserDB))
        session.commit()
    login_attempt_repo.clear_all()
    
    with app.test_client() as client:
        yield client

def test_mfa_lockout_increment(client):
    # 1. User mit aktiviertem MFA anlegen
    username = "mfa_user"
    password = "password123!"
    secret = pyotp.random_base32()
    
    user = UserDB(
        username=username,
        password_hash=generate_password_hash(password),
        role="user",
        mfa_enabled=True,
        mfa_secret=encrypt_secret(secret),
        failed_login_attempts=0
    )
    user_repo.save(user)
    
    # 2. Login mit korrektem Passwort aber falschem MFA-Token
    for i in range(3):
        response = client.post("/login", json={
            "username": username,
            "password": password,
            "mfa_token": "000000"
        })
        assert response.status_code == 401
        assert response.json["message"] == "Invalid MFA token"
        
    # 3. Prüfen ob failed_login_attempts erhöht wurde
    updated_user = user_repo.get_by_username(username)
    assert updated_user.failed_login_attempts == 3

def test_refresh_token_rotation(client):
    # 1. User anlegen und einloggen
    username = "refresh_user"
    password = "password123!"
    user_repo.save(UserDB(
        username=username,
        password_hash=generate_password_hash(password),
        role="user"
    ))
    
    login_response = client.post("/login", json={
        "username": username,
        "password": password
    })
    assert login_response.status_code == 200
    old_refresh_token = login_response.json["data"]["refresh_token"]
    
    # Kurze Pause um sicherzustellen, dass iat/exp sich ändern könnten (falls nötig)
    time.sleep(1)
    
    # 2. Token refreshen
    refresh_response = client.post("/refresh-token", json={
        "refresh_token": old_refresh_token
    })
    assert refresh_response.status_code == 200
    new_refresh_token = refresh_response.json["data"].get("refresh_token")
    
    # 3. Prüfen ob Rotation stattgefunden hat
    # Wenn Rotation implementiert ist, sollte new_refresh_token != old_refresh_token sein
    # UND das alte Token sollte ungültig sein.
    
    # Da wir wissen, dass es aktuell noch NICHT implementiert ist, 
    # wird dieser Test vermutlich erst mal fehlschlagen (bzw. die Assertion für Rotation)
    assert new_refresh_token is not None, "Refresh Token sollte im Response enthalten sein"
    assert new_refresh_token != old_refresh_token, "Refresh Token sollte rotiert werden"
    
    # 4. Altes Token sollte nun ungültig sein
    second_refresh_response = client.post("/refresh-token", json={
        "refresh_token": old_refresh_token
    })
    assert second_refresh_response.status_code == 401

def test_mfa_setup_and_login_flow(client):
    username = "mfa_flow_user"
    password = "password123!"
    user_repo.save(UserDB(
        username=username,
        password_hash=generate_password_hash(password),
        role="user"
    ))

    login_response = client.post("/login", json={
        "username": username,
        "password": password
    })
    assert login_response.status_code == 200
    access_token = login_response.json["data"]["access_token"]

    setup_response = client.post("/mfa/setup", headers={
        "Authorization": f"Bearer {access_token}"
    })
    assert setup_response.status_code == 200
    secret = setup_response.json["data"]["secret"]

    token = pyotp.TOTP(secret).now()
    verify_response = client.post("/mfa/verify", json={"token": token}, headers={
        "Authorization": f"Bearer {access_token}"
    })
    assert verify_response.status_code == 200
    assert verify_response.json["data"].get("status") == "mfa_enabled"
    assert len(verify_response.json["data"].get("backup_codes", [])) == 10

    login_without_mfa = client.post("/login", json={
        "username": username,
        "password": password
    })
    assert login_without_mfa.status_code == 200
    assert login_without_mfa.json["data"].get("mfa_required") is True

    fresh_token = pyotp.TOTP(secret).now()
    login_with_mfa = client.post("/login", json={
        "username": username,
        "password": password,
        "mfa_token": fresh_token
    })
    assert login_with_mfa.status_code == 200
