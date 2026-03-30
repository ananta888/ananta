from unittest.mock import patch

from agent.db_models import TaskDB
from agent.repository import task_repo


def test_health_exposes_standardized_runtime_shape(client, app):
    with app.app_context():
        app.config["AGENT_NAME"] = "health-agent"
        app.config["APP_STARTED_AT"] = 1000.0
        app.config["AGENT_CONFIG"] = {
            **(app.config.get("AGENT_CONFIG") or {}),
            "default_provider": "ollama",
        }
        app.config["PROVIDER_URLS"] = {
            **(app.config.get("PROVIDER_URLS") or {}),
            "ollama": "http://localhost:11434/api/generate",
        }

    fake_registration = {
        "enabled": True,
        "thread_started": True,
        "running": False,
        "attempts": 1,
        "max_retries": 10,
        "last_attempt_at": 1010.0,
        "last_success_at": 1011.0,
        "last_error": None,
        "next_retry_at": None,
        "registered_as": "health-agent",
    }

    with (
        patch("agent.routes.system.time.time", return_value=1060.0),
        patch("agent.services.system_health_service.get_registration_state", return_value=fake_registration),
        patch("agent.routes.system.http_client.get", return_value=None),
    ):
        response = client.get("/health?basic=1")

    assert response.status_code == 200
    data = response.get_json()["data"]
    assert data["status"] in {"ok", "degraded", "error"}
    assert data["agent"] == "health-agent"
    assert "uptime_seconds" in data
    assert "checks" in data
    assert "shell" in data["checks"]


def test_health_includes_queue_agent_and_registration_sections(client, app):
    from agent.routes.tasks.utils import _update_local_task_status

    with app.app_context():
        _update_local_task_status("health-todo", "todo", title="Todo")
        _update_local_task_status("health-failed", "failed", title="Failed")

    fake_registration = {
        "enabled": True,
        "thread_started": True,
        "running": True,
        "attempts": 2,
        "max_retries": 10,
        "last_attempt_at": 1001.0,
        "last_success_at": None,
        "last_error": "registration_failed",
        "next_retry_at": 1010.0,
        "registered_as": "health-agent",
    }

    with (
        patch("agent.services.system_health_service.get_registration_state", return_value=fake_registration),
        patch("agent.routes.system.http_client.get", return_value=None),
    ):
        response = client.get("/health")

    assert response.status_code == 200
    data = response.get_json()["data"]
    checks = data["checks"]
    assert "queue" in checks
    assert checks["queue"]["counts"]["todo"] >= 1
    assert checks["queue"]["counts"]["failed"] >= 1
    assert "scheduler" in checks
    assert "agents" in checks
    assert "registration" in checks
    assert checks["registration"]["enabled"] is True
    assert checks["registration"]["status"] == "degraded"


def test_health_includes_worker_execution_reconciliation_section(client, app):
    with app.app_context():
        task_repo.save(
            TaskDB(
                id="health-reconcile-1",
                title="Missing worker job",
                status="assigned",
                current_worker_job_id="job-missing-health-1",
            )
        )

    with (
        patch("agent.services.system_health_service.get_registration_state", return_value={"enabled": False}),
        patch("agent.routes.system.http_client.get", return_value=None),
    ):
        response = client.get("/health")

    assert response.status_code == 200
    checks = response.get_json()["data"]["checks"]
    assert "worker_execution_reconciliation" in checks
    assert checks["worker_execution_reconciliation"]["affected_count"] >= 1
    assert checks["worker_execution_reconciliation"]["issue_counts"]["missing_worker_job"] >= 1
