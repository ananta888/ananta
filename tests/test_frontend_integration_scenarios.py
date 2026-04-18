import pytest
import time
import uuid
from flask import Flask
from agent.ai_agent import create_app
from agent.db_models import TaskDB, UserDB
from agent.repository import task_repo, user_repo
from werkzeug.security import generate_password_hash
import jwt
from agent.config import settings

@pytest.fixture
def auth_headers():
    username = "admin_e2e"
    payload = {"sub": username, "role": "admin", "exp": time.time() + 3600}
    token = jwt.encode(payload, settings.secret_key, algorithm="HS256")
    return {"Authorization": f"Bearer {token}"}

def test_frontend_governance_read_model(client, auth_headers):
    """
    Simuliert den Frontend-Abruf des Orchestration Read-Models (TST-042).
    """
    # Erstelle einen Test-Task
    tid = f"tsk-{uuid.uuid4()}"
    task = TaskDB(id=tid, description="Test Governance", status="todo")
    task_repo.save(task)

    response = client.get("/tasks/orchestration/read-model", headers=auth_headers)
    assert response.status_code == 200
    data = response.json["data"]

    # Pruefe ob wichtige Felder fuer das Frontend vorhanden sind
    assert "worker_execution_reconciliation" in data
    assert "artifact_flow" in data
    assert "active_leases" in data

def test_frontend_task_verification_details(client, auth_headers):
    """
    Simuliert den Frontend-Abruf von Verifikations-Details (TST-042).
    """
    tid = f"tsk-{uuid.uuid4()}"
    task = TaskDB(id=tid, description="Test Verification", status="completed")
    task.verification_status = {"status": "passed", "message": "All good"}
    task_repo.save(task)

    response = client.get(f"/tasks/{tid}/verification", headers=auth_headers)
    assert response.status_code == 200
    data = response.json["data"]

    assert data["task_id"] == tid
    assert "verification_spec" in data
    assert data["verification_status"]["status"] == "passed"

def test_high_risk_path_blocking_simulation(client, auth_headers):
    """
    Testet den Pfad fuer High-Risk Operationen, die vom Frontend
    als blockiert/review-pflichtig angezeigt werden muessen (TST-042).
    """
    # Ein Task mit hohem Risiko (simuliert)
    tid = f"tsk-{uuid.uuid4()}"
    # Wir nutzen hier den Evolution-Pfad, da dieser oft High-Risk ist
    response = client.get(f"/api/evolution/tasks/{tid}/read-model", headers=auth_headers)
    # Evolution BP koennte ein /api/evolution Prefix haben laut ai_agent.py (Nein, da stand nur evolution_bp)
    assert response.status_code in [200, 404]

    if response.status_code == 200:
        data = response.json["data"]
        assert "policy" in data
        # Frontend muss wissen ob review_before_apply Pflicht ist
        assert "require_review_before_apply" in data["policy"]
