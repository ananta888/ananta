from types import SimpleNamespace
from unittest.mock import MagicMock


def test_hub_probes_only_registered_worker(client, admin_auth_header, monkeypatch):
    worker = SimpleNamespace(
        name="alpha",
        url="http://ai-agent-alpha:5000",
        role="worker",
        token="worker-token",
        registration_validated=True,
    )
    repository = MagicMock()
    repository.get_all.return_value = [worker]
    health_response = MagicMock(status_code=200)
    health_response.json.return_value = {
        "status": "success",
        "data": {"status": "degraded"},
    }
    ready_response = MagicMock(status_code=503)
    ready_response.json.return_value = {
        "status": "error",
        "data": {
            "ready": False,
            "checks": {"hub": {"status": "ok"}, "llm": {"status": "error"}},
        },
    }
    http = MagicMock()
    http.get.side_effect = [health_response, ready_response]
    monkeypatch.setattr("agent.routes.system.settings.role", "hub")
    monkeypatch.setattr("agent.routes.system.agent_repo", repository)
    monkeypatch.setattr("agent.routes.system.http_client", http)

    response = client.post(
        "/api/system/agents/probe",
        json={"worker_url": worker.url},
        headers=admin_auth_header,
    )

    assert response.status_code == 200
    assert response.json["data"]["health"] == {
        "reachable": True,
        "status": "degraded",
        "http_status": 200,
    }
    assert response.json["data"]["readiness"]["ready"] is False
    assert response.json["data"]["readiness"]["checks"] == {
        "hub": "ok",
        "llm": "error",
    }


def test_hub_probe_rejects_unregistered_url(client, admin_auth_header, monkeypatch):
    repository = MagicMock()
    repository.get_all.return_value = []
    monkeypatch.setattr("agent.routes.system.settings.role", "hub")
    monkeypatch.setattr("agent.routes.system.agent_repo", repository)

    response = client.post(
        "/api/system/agents/probe",
        json={"worker_url": "http://attacker.invalid"},
        headers=admin_auth_header,
    )

    assert response.status_code == 404
    assert response.json["message"] == "registered_worker_required"
