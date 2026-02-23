from sqlmodel import Session, select

from agent.config import settings
from agent.db_models import PasswordHistoryDB, RefreshTokenDB, UserDB
from agent.database import engine
from werkzeug.security import generate_password_hash


def test_test_provision_and_delete_user_endpoints(client):
    settings.auth_test_endpoints_enabled = True
    username = "test_provision_user"
    password = "ProvisionUser1!A"

    create_res = client.post(
      "/test/provision-user",
      json={"username": username, "password": password, "role": "user", "overwrite": True},
    )
    assert create_res.status_code == 200

    with Session(engine) as session:
        user = session.get(UserDB, username)
        assert user is not None
        session.add(PasswordHistoryDB(username=username, password_hash=generate_password_hash("OldPass1!A")))
        session.add(RefreshTokenDB(token="tok-provision", username=username, expires_at=9999999999))
        session.commit()

    delete_res = client.delete(f"/test/users/{username}")
    assert delete_res.status_code == 200

    with Session(engine) as session:
        assert session.get(UserDB, username) is None
        assert session.exec(select(PasswordHistoryDB).where(PasswordHistoryDB.username == username)).all() == []
        assert session.exec(select(RefreshTokenDB).where(RefreshTokenDB.username == username)).all() == []


def test_test_reset_user_auth_state(client):
    settings.auth_test_endpoints_enabled = True
    username = "test_reset_user"
    password = "ResetUser1!A"

    provision = client.post("/test/provision-user", json={"username": username, "password": password, "overwrite": True})
    assert provision.status_code == 200

    with Session(engine) as session:
        user = session.get(UserDB, username)
        assert user is not None
        user.mfa_enabled = True
        user.mfa_secret = "secret"
        user.mfa_backup_codes = ["code-hash"]
        user.failed_login_attempts = 5
        user.lockout_until = 9999999999
        session.add(user)
        session.commit()

    reset = client.post("/test/reset-user-auth-state", json={"username": username, "password": password})
    assert reset.status_code == 200

    with Session(engine) as session:
        user = session.get(UserDB, username)
        assert user is not None
        assert user.mfa_enabled is False
        assert user.mfa_secret is None
        assert user.mfa_backup_codes == []
        assert user.failed_login_attempts == 0
        assert user.lockout_until is None
