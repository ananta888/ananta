from __future__ import annotations

import pytest

from agent.services.ops_models import (
    ComposeProjectSummary,
    DockerContainerSummary,
    DockerEngineStatus,
    OpsActionResult,
)


class FakeDockerService:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def status(self):
        return DockerEngineStatus(True, boundary="hub_cli", docker_version="25.0", compose_available=True)

    def info(self):
        return {"ok": True, "info": {"name": "engine", "containers": 1}}

    def containers(self):
        return [
            DockerContainerSummary(
                id="abcdef123456",
                name="hub",
                image="ananta:dev",
                status="Up",
                state="running",
                managed=True,
                allowed_actions=["logs", "inspect_light", "stats", "restart"],
            )
        ]

    def container_snapshot(self):
        items = [item.to_dict() for item in self.containers()]
        return {"ok": True, "items": items, "count": len(items), "truncated": False, "error": None}

    def images(self):
        return {"ok": True, "items": [{"id": "sha256:1"}], "count": 1, "truncated": False}

    def networks(self):
        return {"ok": True, "items": [{"id": "net-1"}], "count": 1, "truncated": False}

    def volumes(self):
        return {"ok": True, "items": [{"name": "data"}], "count": 1, "truncated": False}

    def disk_usage(self):
        return {"ok": True, "items": [{"type": "Images"}], "count": 1, "truncated": False}

    def inspect_light(self, container_id):
        self.calls.append(("inspect", container_id))
        return {"ok": True, "inspect": {"id": container_id}}

    def stats(self, container_id):
        self.calls.append(("stats", container_id))
        return {"ok": True, "stats": {"cpu_percent": "1%"}}

    def logs(self, container_id, *, tail=200, timestamps=False):
        self.calls.append(("logs", container_id, tail, timestamps))
        return {"ok": True, "logs": "line", "tail": 200}

    def action(self, container_id, action, *, approval_id=None):
        self.calls.append(("action", container_id, action, approval_id))
        return OpsActionResult(True, action, target_id=container_id, approval_id=approval_id)


class FakeComposeService:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def projects(self):
        return [
            ComposeProjectSummary(
                project_id="p1",
                name="demo",
                project_directory="/repo",
                compose_files=["compose.yml"],
                allowed_actions=["status", "config", "logs", "pull", "up", "stop", "restart", "down"],
            )
        ]

    def status(self, project_id):
        self.calls.append(("status", project_id))
        return self.projects()[0]

    def config(self, project_id):
        self.calls.append(("config", project_id))
        return {"ok": True, "project_id": project_id, "config": "services: {}"}

    def logs(self, project_id, *, service=None, tail=200, timestamps=False):
        self.calls.append(("logs", project_id, service, tail, timestamps))
        return {"ok": True, "project_id": project_id, "logs": "line"}

    def action(self, project_id, action, *, service=None, approval_id=None):
        self.calls.append(("action", project_id, action, service, approval_id))
        return OpsActionResult(True, action, target_id=project_id, approval_id=approval_id)


@pytest.mark.parametrize(
    ("path", "expected_key"),
    [
        ("/api/ops/docker/info", "info"),
        ("/api/ops/docker/containers", "items"),
        ("/api/ops/docker/images", "items"),
        ("/api/ops/docker/networks", "items"),
        ("/api/ops/docker/volumes", "items"),
        ("/api/ops/docker/disk-usage", "items"),
    ],
)
def test_docker_resource_endpoints_use_common_ops_envelope(client, auth_header, monkeypatch, path, expected_key):
    monkeypatch.setattr("agent.routes.ops.get_docker_engine_service", lambda: FakeDockerService())

    response = client.get(path, headers=auth_header)

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] == "success"
    assert expected_key in payload["data"]


def test_docker_container_detail_routes_forward_bounded_options(client, auth_header, monkeypatch):
    service = FakeDockerService()
    monkeypatch.setattr("agent.routes.ops.get_docker_engine_service", lambda: service)

    inspect = client.get("/api/ops/docker/containers/abcdef123456/inspect", headers=auth_header)
    stats = client.get("/api/ops/docker/containers/abcdef123456/stats", headers=auth_header)
    logs = client.get(
        "/api/ops/docker/containers/abcdef123456/logs?tail=invalid&timestamps=true",
        headers=auth_header,
    )

    assert inspect.status_code == stats.status_code == logs.status_code == 200
    assert ("inspect", "abcdef123456") in service.calls
    assert ("stats", "abcdef123456") in service.calls
    assert ("logs", "abcdef123456", "invalid", True) in service.calls


def test_docker_container_action_forwards_one_shot_approval(client, admin_auth_header, monkeypatch):
    service = FakeDockerService()
    monkeypatch.setattr("agent.routes.ops.get_docker_engine_service", lambda: service)

    response = client.post(
        "/api/ops/docker/containers/abcdef123456/action",
        headers=admin_auth_header,
        json={"action": "restart", "approval_id": "grant-1"},
    )

    assert response.status_code == 200
    assert ("action", "abcdef123456", "restart", "grant-1") in service.calls


def test_docker_container_action_remains_admin_only(client, user_auth_header, monkeypatch):
    service = FakeDockerService()
    monkeypatch.setattr("agent.routes.ops.get_docker_engine_service", lambda: service)

    response = client.post(
        "/api/ops/docker/containers/abcdef123456/action",
        headers=user_auth_header,
        json={"action": "restart"},
    )

    assert response.status_code == 403
    assert not any(call[0] == "action" for call in service.calls)


def test_compose_read_routes_forward_service_and_timestamps(client, auth_header, monkeypatch):
    service = FakeComposeService()
    monkeypatch.setattr("agent.routes.ops.get_docker_compose_service", lambda: service)

    projects = client.get("/api/ops/compose/projects", headers=auth_header)
    status = client.get("/api/ops/compose/projects/p1/status", headers=auth_header)
    config = client.get("/api/ops/compose/projects/p1/config", headers=auth_header)
    logs = client.get(
        "/api/ops/compose/projects/p1/logs?service=hub&tail=invalid&timestamps=on",
        headers=auth_header,
    )

    assert projects.status_code == status.status_code == config.status_code == logs.status_code == 200
    assert ("status", "p1") in service.calls
    assert ("config", "p1") in service.calls
    assert ("logs", "p1", "hub", "invalid", True) in service.calls


@pytest.mark.parametrize("action", ["pull", "up", "stop", "restart", "down"])
def test_compose_action_contract_supports_safe_lifecycle_actions(client, admin_auth_header, monkeypatch, action):
    service = FakeComposeService()
    monkeypatch.setattr("agent.routes.ops.get_docker_compose_service", lambda: service)

    response = client.post(
        "/api/ops/compose/projects/p1/action",
        headers=admin_auth_header,
        json={"action": action, "approval_id": "grant-compose"},
    )

    assert response.status_code == 200
    assert ("action", "p1", action, None, "grant-compose") in service.calls


def test_compose_action_forwards_registered_service_target(client, admin_auth_header, monkeypatch):
    service = FakeComposeService()
    monkeypatch.setattr("agent.routes.ops.get_docker_compose_service", lambda: service)

    response = client.post(
        "/api/ops/compose/projects/p1/action",
        headers=admin_auth_header,
        json={"action": "restart", "service": "worker", "approval_id": "grant-service"},
    )

    assert response.status_code == 200
    assert ("action", "p1", "restart", "worker", "grant-service") in service.calls
