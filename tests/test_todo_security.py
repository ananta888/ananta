from unittest.mock import patch

import pytest
from sqlmodel import Session, delete
from werkzeug.security import generate_password_hash

from agent.ai_agent import create_app
from agent.database import engine
from agent.db_models import RefreshTokenDB, UserDB
from agent.repository import login_attempt_repo, user_repo


@pytest.fixture
def client():
    app = create_app()
    app.config["TESTING"] = True

    login_attempt_repo.clear_all()
    with Session(engine) as session:
        session.exec(delete(RefreshTokenDB))
        session.exec(delete(UserDB))
        session.commit()

    user_repo.save(UserDB(username="testuser", password_hash=generate_password_hash("TestPassword123!"), role="user"))

    with app.test_client() as client:
        yield client


def test_refresh_token_rate_limiting(client):
    # 10 Versuche sind erlaubt (IP-basiert) laut is_rate_limited in auth.py
    for i in range(10):
        response = client.post("/refresh-token", json={"refresh_token": "some-token"})
        # Rate Limiting für /refresh-token ist implementiert.
        assert response.status_code != 429

    # Der 11. Versuch sollte 429 liefern, wenn es implementiert ist.
    response = client.post("/refresh-token", json={"refresh_token": "some-token"})
    assert response.status_code == 429


def test_password_history(client):
    # Login um Token zu bekommen
    response = client.post("/login", json={"username": "testuser", "password": "TestPassword123!"})
    token = response.json["data"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    passwords = ["NewPass123!_1", "NewPass123!_2", "NewPass123!_3"]

    # Ändere Passwort 3 mal
    current_old = "TestPassword123!"
    for p in passwords:
        response = client.post(
            "/change-password", headers=headers, json={"old_password": current_old, "new_password": p}
        )
        assert response.status_code == 200
        current_old = p

    # Jetzt versuche eines der alten Passwörter zu verwenden (das erste: TestPassword123!)
    response = client.post(
        "/change-password", headers=headers, json={"old_password": current_old, "new_password": "TestPassword123!"}
    )
    assert response.status_code == 400
    assert b"cannot reuse" in response.data.lower()

    # Versuche das 2. Passwort (NewPass123!_1)
    response = client.post(
        "/change-password", headers=headers, json={"old_password": current_old, "new_password": "NewPass123!_1"}
    )
    assert response.status_code == 400
    assert b"cannot reuse" in response.data.lower()

    # Ein 4. Passwort sollte funktionieren
    response = client.post(
        "/change-password", headers=headers, json={"old_password": current_old, "new_password": "NewPass123!_4"}
    )
    assert response.status_code == 200


def test_account_lockout_notification(client, caplog):
    with patch("agent.routes.auth._log") as mock_log_factory:
        mock_log = mock_log_factory.return_value
        # 5 Fehlversuche
        for i in range(5):
            response = client.post("/login", json={"username": "testuser", "password": "wrong-password"})
            assert response.status_code == 401

        mock_log.info.assert_any_call("Sending notification email to admin and user %s", "testuser")

        # Der 6. Versuch sollte 403 liefern (locked)
        response = client.post("/login", json={"username": "testuser", "password": "wrong-password"})
        assert response.status_code == 403

    # Prüfe Audit Log (indem wir direkt in die DB schauen)
    from agent.repository import audit_repo

    logs = audit_repo.get_all()
    lockout_logs = [entry for entry in logs if entry.action == "account_lockout"]
    assert len(lockout_logs) > 0
    assert lockout_logs[0].details["username"] == "testuser"
    assert lockout_logs[0].details["severity"] == "CRITICAL"
