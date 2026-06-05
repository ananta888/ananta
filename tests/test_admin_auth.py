import os

import pytest
from sqlmodel import Session

from agent.ai_agent import create_app
from agent.common.audit import log_audit
from agent.database import engine
from agent.repository import audit_repo, login_attempt_repo
from tests_support import admin_login_token as _login_admin


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
    token = _login_admin(client)
    headers = {"Authorization": f"Bearer {token}"}

    # 2. User Liste abrufen
    response = client.get("/users", headers=headers)
    assert response.status_code == 200
    assert len(response.json["data"]) >= 1

    # 3. Neuen User anlegen
    response = client.post(
        "/users", headers=headers, json={"username": "testuser", "password": "TestPassword123!", "role": "user"}
    )
    assert response.status_code == 200

    # 4. Prüfen ob User existiert
    response = client.get("/users", headers=headers)
    assert any(u["username"] == "testuser" for u in response.json["data"])

    # 5. Passwort resetten
    response = client.post(
        "/users/testuser/reset-password", headers=headers, json={"new_password": "NewTestPassword123!"}
    )
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
    from werkzeug.security import generate_password_hash

    from agent.db_models import UserDB
    from agent.repository import user_repo

    username = "lockout_test"
    password = "password123!"
    user_repo.save(UserDB(username=username, password_hash=generate_password_hash(password), role="user"))

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


def test_admin_routes_create_audit_entries(client):
    token = _login_admin(client)
    headers = {"Authorization": f"Bearer {token}"}

    before_count = len(audit_repo.get_all(limit=500))

    response = client.get("/audit-logs", headers=headers)
    assert response.status_code == 200

    after_logs = audit_repo.get_all(limit=500)
    assert len(after_logs) >= before_count + 2
    recent_actions = [log.action for log in after_logs[:10]]
    assert "admin_route_accessed" in recent_actions
    assert "audit_logs_viewed" in recent_actions


def test_audit_logs_query_filters_and_summary(client):
    token = _login_admin(client)
    headers = {"Authorization": f"Bearer {token}"}

    log_audit(
        "kritis_high_risk_flow",
        {
            "trace_id": "trace-kritis-1",
            "task_id": "task-kritis-1",
            "operation_type": "workflow_transition",
            "outcome": "blocked",
        },
    )
    log_audit(
        "kritis_low_risk_flow",
        {
            "trace_id": "trace-kritis-2",
            "task_id": "task-kritis-2",
            "operation_type": "tool_call",
            "outcome": "success",
        },
    )

    filtered = client.get("/audit-logs?trace_id=trace-kritis-1", headers=headers)
    assert filtered.status_code == 200
    filtered_items = filtered.json["data"]
    assert len(filtered_items) >= 1
    assert all(item.get("trace_id") == "trace-kritis-1" for item in filtered_items)

    event_class_filtered = client.get("/audit-logs?event_class=workflow_transition", headers=headers)
    assert event_class_filtered.status_code == 200
    class_items = event_class_filtered.json["data"]
    assert len(class_items) >= 1
    assert all((item.get("details") or {}).get("operation_type") == "workflow_transition" for item in class_items)

    summary = client.get("/audit-logs/summary", headers=headers)
    assert summary.status_code == 200
    summary_data = summary.json["data"]
    assert summary_data["total_events"] >= 2
    assert summary_data["critical_events"] >= 1
    assert "top_actions" in summary_data


def test_audit_logs_integrity_report(client):
    token = _login_admin(client)
    headers = {"Authorization": f"Bearer {token}"}

    log_audit("kritis_integrity_probe_a", {"trace_id": "integrity-trace-a"})
    log_audit("kritis_integrity_probe_b", {"trace_id": "integrity-trace-b"})

    response = client.get("/audit-logs/integrity", headers=headers)
    assert response.status_code == 200
    payload = response.json["data"]
    assert payload["checked_records"] >= 2
    assert payload["tamper_evident_ok"] is True
    assert payload["mismatched_prev_hash_ids"] == []
    assert payload["invalid_record_hash_ids"] == []
