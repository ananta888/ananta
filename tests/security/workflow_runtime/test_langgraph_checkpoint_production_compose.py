from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
BASE_COMPOSE = ROOT / "docker" / "compose-next" / "compose.base.yml"
STACK_COMPOSE = ROOT / "docker" / "compose-next" / "compose.stack.full.yml"
SECURITY_OVERLAY = ROOT / "docker" / "compose-next" / "compose.workflow-runtime.production.yml"
PRODUCTION_OVERLAY = ROOT / "docker" / "compose-next" / "compose.langgraph.production.yml"
DOCKERFILE = ROOT / "docker" / "compose-next" / "Dockerfile.quickstart-no-ollama"
RUNTIME_LOCK = ROOT / "docker" / "compose-next" / "requirements.langgraph-worker.lock"
RUNBOOK = ROOT / "docs" / "operations" / "langgraph-hub-checkpoint-runtime.md"

AUTH_SIGNING_KEYRING = "workflow_runtime_auth_signing_keyring"
AUTH_VERIFICATION_KEYRING = "workflow_runtime_auth_verification_keyring"
DISPATCH_KEYRING = "workflow_runtime_dispatch_keyring"
HUB_TOKEN = "workflow_hub_service_token"
LANGGRAPH_REGISTRATION_TOKEN = "workflow_worker_langgraph_registration_token"
LANGGRAPH_SERVICE_TOKEN = "workflow_worker_langgraph_service_token"
LANGGRAPH_SESSION_KEY = "workflow_worker_langgraph_session_signing_key"
RUNTIME_NETWORK = "langgraph-runtime"
LANGGRAPH_WORKER = "ai-agent-langgraph-worker"
LANGGRAPH_WORKSPACE_VOLUME = "workflow_langgraph_worker_workspace"
LANGGRAPH_ALLOWED_CAPABILITIES = {
    "planning",
    "analysis",
    "research",
    "source_analysis",
    "coding",
    "implementation",
    "review",
    "testing",
    "verification",
    "workflow.adapter.langgraph",
}


class _ComposeLoader(yaml.SafeLoader):
    pass


def _construct_compose_tag(loader: _ComposeLoader, node: yaml.Node) -> Any:
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node, deep=True)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node, deep=True)
    return loader.construct_scalar(node)


_ComposeLoader.add_constructor("!override", _construct_compose_tag)
_ComposeLoader.add_constructor("!reset", _construct_compose_tag)


def _load(path: Path) -> dict:
    document = yaml.load(path.read_text(encoding="utf-8"), Loader=_ComposeLoader)
    assert isinstance(document, dict)
    return document


def _secret_sources(service: dict) -> set[str]:
    return {str(binding["source"] if isinstance(binding, dict) else binding) for binding in service.get("secrets", ())}


def _assert_read_only_secret_bindings(service: dict) -> None:
    for binding in service.get("secrets", ()):
        assert isinstance(binding, dict)
        assert binding["target"] == binding["source"]
        assert int(binding["mode"]) & 0o222 == 0


def _mount_at(service: dict, target: str) -> dict:
    matches = [
        mount
        for mount in service.get("volumes", ())
        if isinstance(mount, dict) and mount.get("target") == target
    ]
    assert len(matches) == 1
    return matches[0]


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
    runbook = RUNBOOK.read_text(encoding="utf-8")
    assert '"allowed_capabilities": [' in runbook
    assert all(f'"{capability}"' in runbook for capability in LANGGRAPH_ALLOWED_CAPABILITIES)
    assert "strict_registration_keyring_v1" in runbook


def test_production_overlay_mounts_external_workflow_secrets_read_only() -> None:
    overlay = _load(PRODUCTION_OVERLAY)
    assert set(overlay["secrets"]) == {
        LANGGRAPH_REGISTRATION_TOKEN,
        LANGGRAPH_SERVICE_TOKEN,
        LANGGRAPH_SESSION_KEY,
    }
    for secret in overlay["secrets"].values():
        reference = str(secret["file"])
        assert reference.startswith("${ANANTA_")
        assert ":?Error:" in reference

    rendered = PRODUCTION_OVERLAY.read_text(encoding="utf-8")
    assert "AGENT_TOKEN:" not in rendered
    assert "active_key_id:" not in rendered
    assert "Bearer " not in rendered


def test_hub_and_only_dedicated_langgraph_worker_receive_required_secrets() -> None:
    hub = _load(SECURITY_OVERLAY)["services"]["ai-agent-hub"]
    worker = _load(PRODUCTION_OVERLAY)["services"][LANGGRAPH_WORKER]

    assert _secret_sources(hub) == {
        AUTH_SIGNING_KEYRING,
        DISPATCH_KEYRING,
        HUB_TOKEN,
        "workflow_hub_session_signing_key",
        "workflow_worker_registration_keyring",
    }
    assert _secret_sources(worker) == {
        AUTH_VERIFICATION_KEYRING,
        LANGGRAPH_REGISTRATION_TOKEN,
        LANGGRAPH_SERVICE_TOKEN,
        LANGGRAPH_SESSION_KEY,
    }
    _assert_read_only_secret_bindings(hub)
    _assert_read_only_secret_bindings(worker)
    assert worker["environment"]["ANANTA_LANGGRAPH_HUB_URL"] == ("http://ai-agent-hub:5000")
    assert worker["environment"]["ANANTA_LANGGRAPH_HUB_TOKEN_FILE"] == (f"/run/secrets/{LANGGRAPH_SERVICE_TOKEN}")
    assert worker["environment"]["AGENT_TOKEN_FILE"] == (f"/run/secrets/{LANGGRAPH_SERVICE_TOKEN}")
    assert worker["environment"]["REGISTRATION_TOKEN_FILE"] == (f"/run/secrets/{LANGGRAPH_REGISTRATION_TOKEN}")
    assert worker["environment"]["SECRET_KEY_FILE"] == (f"/run/secrets/{LANGGRAPH_SESSION_KEY}")
    assert worker["environment"]["DISABLE_INITIAL_ADMIN"] == "1"
    assert {
        "SECRET_KEY",
        "INITIAL_ADMIN_USER",
        "INITIAL_ADMIN_PASSWORD",
        "DATABASE_URL",
        "REDIS_URL",
    }.isdisjoint(worker["environment"])
    assert hub["environment"]["AGENT_TOKEN_FILE"] == f"/run/secrets/{HUB_TOKEN}"
    assert hub["environment"]["ANANTA_WORKFLOW_AUTH_SIGNING_KEYRING_FILE"] == (f"/run/secrets/{AUTH_SIGNING_KEYRING}")
    assert worker["environment"]["ANANTA_WORKFLOW_AUTH_VERIFICATION_KEYRING_FILE"] == (
        f"/run/secrets/{AUTH_VERIFICATION_KEYRING}"
    )
    assert DISPATCH_KEYRING not in _secret_sources(worker)
    assert AUTH_SIGNING_KEYRING not in _secret_sources(worker)
    assert HUB_TOKEN not in _secret_sources(worker)


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


def test_langgraph_worker_uses_a_dedicated_non_host_workspace_volume() -> None:
    overlay = _load(PRODUCTION_OVERLAY)
    worker = overlay["services"][LANGGRAPH_WORKER]
    workspace = _mount_at(worker, "/project-workspaces")

    assert LANGGRAPH_WORKSPACE_VOLUME in overlay["volumes"]
    assert workspace == {
        "type": "volume",
        "source": LANGGRAPH_WORKSPACE_VOLUME,
        "target": "/project-workspaces",
        "volume": {"nocopy": True},
    }
    assert all(
        not isinstance(mount, str) or "project-workspaces" not in mount
        for mount in worker["volumes"]
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
        "CORS_ORIGINS": "https://ananta.example.test",
        "ANANTA_WORKFLOW_AUTH_SIGNING_KEYRING_SECRET_FILE": "/tmp/workflow-auth-signing.json",
        "ANANTA_WORKFLOW_AUTH_VERIFICATION_KEYRING_SECRET_FILE": "/tmp/workflow-auth-verification.json",
        "ANANTA_WORKFLOW_DISPATCH_KEYRING_SECRET_FILE": "/tmp/workflow-dispatch.json",
        "ANANTA_WORKFLOW_HUB_TOKEN_SECRET_FILE": "/tmp/workflow-token",
        "ANANTA_HUB_SESSION_SIGNING_KEY_SECRET_FILE": "/tmp/workflow-hub-session",
        "ANANTA_WORKFLOW_WORKER_REGISTRATION_KEYRING_SECRET_FILE": "/tmp/workflow-worker-registration-keyring",
        "ANANTA_WORKFLOW_WORKER_ALPHA_REGISTRATION_TOKEN_SECRET_FILE": "/tmp/workflow-alpha-registration-token",
        "ANANTA_WORKFLOW_WORKER_BETA_REGISTRATION_TOKEN_SECRET_FILE": "/tmp/workflow-beta-registration-token",
        "ANANTA_WORKFLOW_WORKER_ALPHA_SERVICE_TOKEN_SECRET_FILE": "/tmp/workflow-alpha-service-token",
        "ANANTA_WORKFLOW_WORKER_BETA_SERVICE_TOKEN_SECRET_FILE": "/tmp/workflow-beta-service-token",
        "ANANTA_WORKER_ALPHA_SESSION_SIGNING_KEY_SECRET_FILE": "/tmp/workflow-alpha-session",
        "ANANTA_WORKER_BETA_SESSION_SIGNING_KEY_SECRET_FILE": "/tmp/workflow-beta-session",
        "ANANTA_WORKFLOW_WORKER_LANGGRAPH_REGISTRATION_TOKEN_SECRET_FILE": "/tmp/workflow-langgraph-registration-token",
        "ANANTA_WORKFLOW_WORKER_LANGGRAPH_SERVICE_TOKEN_SECRET_FILE": "/tmp/workflow-langgraph-service-token",
        "ANANTA_WORKER_LANGGRAPH_SESSION_SIGNING_KEY_SECRET_FILE": "/tmp/workflow-langgraph-session",
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
            str(SECURITY_OVERLAY),
            "-f",
            str(PRODUCTION_OVERLAY),
            "--profile",
            "langgraph",
            "config",
            "--format",
            "json",
        ],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    rendered = json.loads(completed.stdout)
    worker = rendered["services"][LANGGRAPH_WORKER]
    hub = rendered["services"]["ai-agent-hub"]
    assert HUB_TOKEN not in _secret_sources(worker)
    assert {
        "SECRET_KEY",
        "INITIAL_ADMIN_USER",
        "INITIAL_ADMIN_PASSWORD",
        "DATABASE_URL",
        "REDIS_URL",
    }.isdisjoint(worker["environment"])
    workspace = _mount_at(worker, "/project-workspaces")
    hub_workspace = _mount_at(hub, "/project-workspaces")
    assert workspace["type"] == "volume"
    assert workspace["source"] == LANGGRAPH_WORKSPACE_VOLUME
    assert workspace["volume"]["nocopy"] is True
    assert all(mount["type"] != "bind" for mount in worker["volumes"])
    assert hub_workspace["type"] == "bind"
    assert hub_workspace["source"] != workspace["source"]
