import time

import jwt
import pyotp
import pytest
from sqlmodel import Session, delete
from werkzeug.security import generate_password_hash

from agent.ai_agent import create_app
from agent.common.mfa import decrypt_secret, encrypt_secret
from agent.config import settings
from agent.database import engine
from agent.db_models import RefreshTokenDB, UserDB
from agent.repository import login_attempt_repo, user_repo
from agent.services.user_session_tokens import local_user_tenant_id


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
        failed_login_attempts=0,
    )
    user_repo.save(user)

    # 2. Login mit korrektem Passwort aber falschem MFA-Token
    for i in range(3):
        response = client.post("/login", json={"username": username, "password": password, "mfa_token": "000000"})
        assert response.status_code == 401
        assert response.json["message"] == "Invalid MFA token"

    # 3. Prüfen ob failed_login_attempts erhöht wurde
    updated_user = user_repo.get_by_username(username)
    assert updated_user.failed_login_attempts == 3


def test_refresh_token_rotation(client):
    # 1. User anlegen und einloggen
    username = "refresh_user"
    password = "password123!"
    user_repo.save(UserDB(username=username, password_hash=generate_password_hash(password), role="user"))

    login_response = client.post("/login", json={"username": username, "password": password})
    assert login_response.status_code == 200
    login_claims = jwt.decode(
        login_response.json["data"]["access_token"],
        settings.secret_key,
        algorithms=["HS256"],
    )
    assert login_claims["tenant_id"] == local_user_tenant_id(username)
    old_refresh_token = login_response.json["data"]["refresh_token"]

    # Kurze Pause um sicherzustellen, dass iat/exp sich ändern könnten (falls nötig)
    time.sleep(1)

    # 2. Token refreshen
    refresh_response = client.post("/refresh-token", json={"refresh_token": old_refresh_token})
    assert refresh_response.status_code == 200
    new_refresh_token = refresh_response.json["data"].get("refresh_token")
    refresh_claims = jwt.decode(
        refresh_response.json["data"]["access_token"],
        settings.secret_key,
        algorithms=["HS256"],
    )
    assert refresh_claims["tenant_id"] == login_claims["tenant_id"]

    # 3. Prüfen ob Rotation stattgefunden hat
    # Rotation ist implementiert: new_refresh_token != old_refresh_token
    # UND das alte Token sollte ungültig sein.
    assert new_refresh_token is not None, "Refresh Token sollte im Response enthalten sein"
    assert new_refresh_token != old_refresh_token, "Refresh Token sollte rotiert werden"

    # 4. Altes Token sollte nun ungültig sein
    second_refresh_response = client.post("/refresh-token", json={"refresh_token": old_refresh_token})
    assert second_refresh_response.status_code == 401


def test_mfa_setup_and_login_flow(client):
    username = "mfa_flow_user"
    password = "password123!"
    user_repo.save(UserDB(username=username, password_hash=generate_password_hash(password), role="user"))

    login_response = client.post("/login", json={"username": username, "password": password})
    assert login_response.status_code == 200
    access_token = login_response.json["data"]["access_token"]
    initial_claims = jwt.decode(access_token, settings.secret_key, algorithms=["HS256"])
    assert initial_claims["tenant_id"] == local_user_tenant_id(username)

    setup_response = client.post("/mfa/setup", headers={"Authorization": f"Bearer {access_token}"})
    assert setup_response.status_code == 200
    setup_secret = setup_response.json["data"]["secret"]
    assert setup_secret
    user = user_repo.get_by_username(username)
    assert user and user.mfa_secret
    secret = decrypt_secret(user.mfa_secret)

    token = pyotp.TOTP(secret).now()
    verify_response = client.post(
        "/mfa/verify", json={"token": token}, headers={"Authorization": f"Bearer {access_token}"}
    )
    assert verify_response.status_code == 200
    assert verify_response.json["data"].get("status") == "mfa_enabled"
    assert len(verify_response.json["data"].get("backup_codes", [])) == 10
    verified_access_token = verify_response.json["data"]["access_token"]
    verified_claims = jwt.decode(verified_access_token, settings.secret_key, algorithms=["HS256"])
    assert verified_claims["tenant_id"] == initial_claims["tenant_id"]
    assert verified_claims["mfa_enabled"] is True

    login_without_mfa = client.post("/login", json={"username": username, "password": password})
    assert login_without_mfa.status_code == 200
    assert login_without_mfa.json["data"].get("mfa_required") is True

    fresh_token = pyotp.TOTP(secret).now()
    login_with_mfa = client.post("/login", json={"username": username, "password": password, "mfa_token": fresh_token})
    assert login_with_mfa.status_code == 200
    mfa_login_claims = jwt.decode(
        login_with_mfa.json["data"]["access_token"],
        settings.secret_key,
        algorithms=["HS256"],
    )
    assert mfa_login_claims["tenant_id"] == initial_claims["tenant_id"]

    disable_response = client.post(
        "/mfa/disable",
        headers={"Authorization": f"Bearer {verified_access_token}"},
    )
    assert disable_response.status_code == 200
    disabled_claims = jwt.decode(
        disable_response.json["data"]["access_token"],
        settings.secret_key,
        algorithms=["HS256"],
    )
    assert disabled_claims["tenant_id"] == initial_claims["tenant_id"]
    assert disabled_claims["mfa_enabled"] is False
