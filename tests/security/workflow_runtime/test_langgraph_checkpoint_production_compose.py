from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
BASE_COMPOSE = ROOT / "docker" / "compose-next" / "compose.base.yml"
STACK_COMPOSE = ROOT / "docker" / "compose-next" / "compose.stack.full.yml"
PRODUCTION_OVERLAY = ROOT / "docker" / "compose-next" / "compose.langgraph.production.yml"
DOCKERFILE = ROOT / "docker" / "compose-next" / "Dockerfile.quickstart-no-ollama"
RUNTIME_LOCK = ROOT / "docker" / "compose-next" / "requirements.langgraph-worker.lock"

AUTH_KEYRING = "workflow_runtime_auth_keyring"
DISPATCH_KEYRING = "workflow_runtime_dispatch_keyring"
HUB_TOKEN = "workflow_hub_service_token"
RUNTIME_NETWORK = "langgraph-runtime"
LANGGRAPH_WORKER = "ai-agent-langgraph-worker"


def _load(path: Path) -> dict:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


def _secret_sources(service: dict) -> set[str]:
    return {str(binding["source"] if isinstance(binding, dict) else binding) for binding in service.get("secrets", ())}


def _assert_read_only_secret_bindings(service: dict) -> None:
    for binding in service.get("secrets", ()):
        assert isinstance(binding, dict)
        assert binding["target"] == binding["source"]
        assert int(binding["mode"]) & 0o222 == 0


def test_langgraph_worker_runtime_lock_and_image_are_exact_and_opt_in() -> None:
    requirements = [
        line.strip()
        for line in RUNTIME_LOCK.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert requirements
    assert all(re.fullmatch(r"[a-z0-9-]+==[^\s]+", line) for line in requirements)
    assert "langgraph==0.2.76" in requirements
    assert "langchain-core==0.3.86" in requirements

    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    assert "ARG INSTALL_LANGGRAPH_RUNTIME=0" in dockerfile
    assert "requirements.langgraph-worker.lock" in dockerfile
    assert "INSTALL_LANGGRAPH_RUNTIME must be 0 or 1" in dockerfile
    worker_build = _load(PRODUCTION_OVERLAY)["services"][LANGGRAPH_WORKER]["build"]
    assert worker_build["args"]["INSTALL_LANGGRAPH_RUNTIME"] == "1"


def test_production_overlay_mounts_external_workflow_secrets_read_only() -> None:
    overlay = _load(PRODUCTION_OVERLAY)
    assert set(overlay["secrets"]) == {AUTH_KEYRING, DISPATCH_KEYRING, HUB_TOKEN}
    for secret in overlay["secrets"].values():
        reference = str(secret["file"])
        assert reference.startswith("${ANANTA_WORKFLOW_")
        assert ":?Error:" in reference

    rendered = PRODUCTION_OVERLAY.read_text(encoding="utf-8")
    assert "AGENT_TOKEN:" not in rendered
    assert "active_key_id:" not in rendered
    assert "Bearer " not in rendered


def test_hub_and_only_dedicated_langgraph_worker_receive_required_secrets() -> None:
    services = _load(PRODUCTION_OVERLAY)["services"]
    hub = services["ai-agent-hub"]
    worker = services[LANGGRAPH_WORKER]

    assert _secret_sources(hub) == {AUTH_KEYRING, DISPATCH_KEYRING, HUB_TOKEN}
    assert _secret_sources(worker) == {HUB_TOKEN}
    _assert_read_only_secret_bindings(hub)
    _assert_read_only_secret_bindings(worker)
    assert worker["environment"]["ANANTA_LANGGRAPH_HUB_URL"] == ("http://ai-agent-hub:5000")
    assert worker["environment"]["ANANTA_LANGGRAPH_HUB_TOKEN_FILE"] == (f"/run/secrets/{HUB_TOKEN}")
    assert worker["environment"]["AGENT_TOKEN_FILE"] == f"/run/secrets/{HUB_TOKEN}"
    assert hub["environment"]["AGENT_TOKEN_FILE"] == f"/run/secrets/{HUB_TOKEN}"
    assert hub["environment"]["ANANTA_WORKFLOW_AUTH_KEYRING_FILE"] == (f"/run/secrets/{AUTH_KEYRING}")
    assert DISPATCH_KEYRING not in _secret_sources(worker)
    assert AUTH_KEYRING not in _secret_sources(worker)


def test_langgraph_worker_has_hub_only_runtime_network_and_no_published_port() -> None:
    overlay = _load(PRODUCTION_OVERLAY)
    services = overlay["services"]
    worker = services[LANGGRAPH_WORKER]
    hub = services["ai-agent-hub"]
    stack_workers = {
        name: service
        for name, service in _load(STACK_COMPOSE)["services"].items()
        if name.startswith("ai-agent-") and name != "ai-agent-hub"
    }

    assert set(worker["networks"]) == {RUNTIME_NETWORK}
    assert RUNTIME_NETWORK in set(hub["networks"])
    assert "ports" not in worker
    assert worker["depends_on"]["ai-agent-hub"]["condition"] == "service_healthy"
    assert all(RUNTIME_NETWORK not in set(service.get("networks", ("default",))) for service in stack_workers.values())
    assert not any(
        "ai-agent-alpha" in str(value) or "ai-agent-beta" in str(value) for value in worker["environment"].values()
    )


def test_dev_and_base_compose_files_do_not_receive_checkpoint_credentials() -> None:
    for path in (BASE_COMPOSE, STACK_COMPOSE):
        rendered = path.read_text(encoding="utf-8")
        assert "ANANTA_LANGGRAPH_HUB_URL" not in rendered
        assert "ANANTA_LANGGRAPH_HUB_TOKEN_FILE" not in rendered
        assert HUB_TOKEN not in rendered


def test_production_overlay_renders_with_docker_compose() -> None:
    if shutil.which("docker") is None:
        pytest.skip("docker CLI is not installed")
    environment = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "INITIAL_ADMIN_PASSWORD": "langgraph-config-test-password",
        "POSTGRES_PASSWORD": "langgraph-config-test-password",
        "ANANTA_WORKFLOW_AUTH_KEYRING_SECRET_FILE": "/tmp/workflow-auth.json",
        "ANANTA_WORKFLOW_DISPATCH_KEYRING_SECRET_FILE": "/tmp/workflow-dispatch.json",
        "ANANTA_WORKFLOW_HUB_TOKEN_SECRET_FILE": "/tmp/workflow-token",
    }
    completed = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            "/dev/null",
            "-f",
            str(STACK_COMPOSE),
            "-f",
            str(PRODUCTION_OVERLAY),
            "--profile",
            "langgraph",
            "config",
            "--quiet",
        ],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
