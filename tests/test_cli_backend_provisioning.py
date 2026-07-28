from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from agent.cli_backends.provisioning import CliBackendProvisioner

ROOT = Path(__file__).resolve().parents[1]


def test_provisioner_installs_only_pinned_catalog_package(tmp_path, monkeypatch):
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        if command[1] == "install":
            prefix = Path(command[command.index("--prefix") + 1])
            binary = prefix / "node_modules" / ".bin" / "codex"
            binary.parent.mkdir(parents=True)
            binary.write_text("#!/bin/sh\n", encoding="utf-8")
            binary.chmod(0o700)
            return SimpleNamespace(returncode=0, stdout="installed", stderr="")
        return SimpleNamespace(returncode=0, stdout="codex-cli 0.145.0", stderr="")

    monkeypatch.setattr(
        "agent.cli_backends.provisioning.shutil.which",
        lambda name: "/usr/bin/npm" if name == "npm" else None,
    )
    provisioner = CliBackendProvisioner(base_dir=tmp_path, run_command=run)

    result = provisioner.install("codex")

    assert result["installed"] is True
    install_command = calls[0][0]
    assert "@openai/codex@0.145.0" in install_command
    assert "--prefix" in install_command
    assert calls[0][1]["timeout"] == 600


def test_worker_provision_route_executes_allowlisted_installer(
    client, admin_auth_header, monkeypatch
):
    provisioner = MagicMock()
    provisioner.install.return_value = {
        "backend": "claude_code",
        "package": "@anthropic-ai/claude-code",
        "version": "2.1.220",
        "installed": True,
        "status": "ready",
    }
    monkeypatch.setattr("agent.routes.sgpt.settings.role", "worker")
    monkeypatch.setattr(
        "agent.cli_backends.provisioning.get_cli_backend_provisioner",
        lambda: provisioner,
    )

    response = client.post(
        "/api/sgpt/backends/claude_code/provision",
        json={"action": "install"},
        headers=admin_auth_header,
    )

    assert response.status_code == 200
    assert response.json["data"]["installed"] is True
    provisioner.install.assert_called_once_with("claude_code")


def test_hub_provision_route_forwards_only_to_registered_worker(
    client, admin_auth_header, monkeypatch
):
    worker = SimpleNamespace(
        name="alpha",
        url="http://worker-alpha:5000",
        role="worker",
        token="w" * 32,
        registration_validated=True,
    )
    gateway = MagicMock()
    gateway.forward_task.return_value = {
        "status": "success",
        "data": {
            "backend": "codex",
            "version": "0.145.0",
            "installed": True,
            "status": "ready",
        },
    }
    monkeypatch.setattr("agent.routes.sgpt.settings.role", "hub")
    monkeypatch.setattr(
        "agent.routes.sgpt._registered_worker",
        lambda url: worker if url == worker.url else None,
    )
    monkeypatch.setattr("agent.routes.sgpt.get_worker_gateway", lambda: gateway)

    response = client.post(
        "/api/sgpt/backends/codex/provision",
        json={"worker_url": worker.url, "action": "install"},
        headers=admin_auth_header,
    )

    assert response.status_code == 200
    assert response.json["data"]["worker"]["name"] == "alpha"
    gateway.forward_task.assert_called_once_with(
        worker.url,
        "/api/sgpt/backends/codex/provision",
        {"action": "install"},
        token=worker.token,
        timeout=620,
    )


def test_hub_provision_route_rejects_unknown_worker(
    client, admin_auth_header, monkeypatch
):
    monkeypatch.setattr("agent.routes.sgpt.settings.role", "hub")
    monkeypatch.setattr("agent.routes.sgpt._registered_worker", lambda _url: None)

    response = client.post(
        "/api/sgpt/backends/codex/provision",
        json={"worker_url": "http://attacker.invalid", "action": "install"},
        headers=admin_auth_header,
    )

    assert response.status_code == 404
    assert response.json["message"] == "registered_worker_required"


def test_hub_routes_cli_diagnose_to_selected_worker(
    client, admin_auth_header, monkeypatch
):
    worker = SimpleNamespace(
        name="ananta-worker-1",
        url="http://worker-alpha:5000",
        role="worker",
        token="w" * 32,
        registration_validated=True,
    )
    gateway = MagicMock()
    gateway.forward_task.return_value = {
        "status": "success",
        "data": {
            "backend": "codex",
            "status": "ready",
            "binary_available": True,
            "binary_path": "/data/codex",
        },
    }
    monkeypatch.setattr("agent.routes.sgpt.settings.role", "hub")
    monkeypatch.setattr(
        "agent.routes.sgpt._registered_worker",
        lambda *args, **kwargs: (
            worker if kwargs.get("worker_name") == worker.name else None
        ),
    )
    monkeypatch.setattr("agent.routes.sgpt.get_worker_gateway", lambda: gateway)

    response = client.post(
        "/api/sgpt/backends/codex/worker-action",
        json={"worker_name": worker.name, "action": "diagnose"},
        headers=admin_auth_header,
    )

    assert response.status_code == 200
    assert response.json["data"]["worker"]["name"] == worker.name
    assert response.json["data"]["binary_available"] is True
    gateway.forward_task.assert_called_once_with(
        worker.url,
        "/api/sgpt/backends/codex/diagnose",
        {},
        token=worker.token,
        timeout=60,
    )


def test_quickstart_image_uses_worker_persistent_cli_backend_directory():
    dockerfile = (
        ROOT / "docker" / "compose-next" / "Dockerfile.quickstart-no-ollama"
    ).read_text(encoding="utf-8")

    assert (
        "ANANTA_CLI_BACKENDS_DIR=/app/data/home/.local/share/ananta/cli-backends"
        in dockerfile
    )
