from __future__ import annotations

from agent.services.ops_models import DockerEngineStatus, OpsError


def test_ops_git_status_requires_auth(client):
    response = client.get("/api/ops/git/status?workspace_id=repo")

    assert response.status_code in {401, 403}


def test_ops_docker_status_returns_api_response(client, auth_header, monkeypatch):
    class FakeDocker:
        def status(self):
            return DockerEngineStatus(
                available=False,
                boundary="disabled",
                error=OpsError("docker_boundary_not_configured", "disabled"),
            )

    monkeypatch.setattr("agent.routes.ops.get_docker_engine_service", lambda: FakeDocker())

    response = client.get("/api/ops/docker/status", headers=auth_header)
    payload = response.get_json()

    assert response.status_code == 403
    assert payload["status"] == "error"
    assert payload["data"]["error"]["code"] == "docker_boundary_not_configured"


def test_ops_compose_projects_returns_items(client, auth_header):
    response = client.get("/api/ops/compose/projects", headers=auth_header)
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["status"] == "success"
    assert "items" in payload["data"]
