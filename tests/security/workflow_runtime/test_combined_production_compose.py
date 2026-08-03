from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
COMPOSE = ROOT / "docker/compose-next"
HUB_TOKEN = "workflow_hub_service_token"
AUTH_VERIFICATION_KEYRING = "workflow_runtime_auth_verification_keyring"
CENTRAL_ONLY_ENVIRONMENT = {
    "DATABASE_URL",
    "REDIS_URL",
    "INITIAL_ADMIN_USER",
    "INITIAL_ADMIN_PASSWORD",
    "SECRET_KEY",
}
EXPECTED_SECRETS = {
    "workflow_runtime_auth_signing_keyring",
    AUTH_VERIFICATION_KEYRING,
    "workflow_runtime_dispatch_keyring",
    HUB_TOKEN,
    "workflow_hub_session_signing_key",
    "workflow_worker_registration_keyring",
    "workflow_worker_alpha_registration_token",
    "workflow_worker_beta_registration_token",
    "workflow_worker_alpha_service_token",
    "workflow_worker_beta_service_token",
    "workflow_worker_alpha_session_signing_key",
    "workflow_worker_beta_session_signing_key",
    "workflow_worker_langgraph_registration_token",
    "workflow_worker_langgraph_service_token",
    "workflow_worker_langgraph_session_signing_key",
    "workflow_runtime_service_keyring",
    "workflow_temporal_service_token",
}
BASE_ALLOWED_CAPABILITIES = [
    "planning",
    "analysis",
    "research",
    "source_analysis",
    "coding",
    "implementation",
    "review",
    "testing",
    "verification",
]
NATIVE_ALLOWED_CAPABILITIES = BASE_ALLOWED_CAPABILITIES + [
    "workflow.adapter.native",
    "approval",
    "bounded_parallel",
    "checkpoint",
    "deterministic_merge",
    "resume",
    "retrieval",
    "stream",
    "structured_output",
    "subgraphs",
    "tool_calling",
]
LANGGRAPH_ALLOWED_CAPABILITIES = BASE_ALLOWED_CAPABILITIES + ["workflow.adapter.langgraph"]


def _secret_sources(service: dict) -> set[str]:
    return {str(binding["source"] if isinstance(binding, dict) else binding) for binding in service.get("secrets", ())}


def _mount_at(service: dict, target: str) -> dict:
    matches = [
        mount
        for mount in service.get("volumes", ())
        if isinstance(mount, dict) and mount.get("target") == target
    ]
    assert len(matches) == 1
    return matches[0]


def test_combined_production_runtime_stack_preserves_one_least_privilege_hub_union(
    tmp_path: Path,
) -> None:
    if shutil.which("docker") is None:
        pytest.skip("docker CLI is not installed")
    registration_keyring = tmp_path / "workflow-worker-registration-keyring.json"
    registration_keyring.write_text(
        json.dumps(
            {
                "schema": "ananta.workflow-worker-registration-keyring.v1",
                "workers": {
                    "ananta-worker-1": {
                        "worker_url": "http://ai-agent-alpha:5000",
                        "registration_token": "alpha-registration-token-0123456789abcdef",
                        "service_token_sha256": hashlib.sha256(
                            b"combined-alpha-service-token-0123456789abcdef"
                        ).hexdigest(),
                        "session_signing_key_sha256": hashlib.sha256(
                            b"combined-alpha-session-key-0123456789abcdef"
                        ).hexdigest(),
                        "allowed_capabilities": NATIVE_ALLOWED_CAPABILITIES,
                    },
                    "ananta-worker-2": {
                        "worker_url": "http://ai-agent-beta:5000",
                        "registration_token": "beta-registration-token-0123456789abcdefg",
                        "service_token_sha256": hashlib.sha256(
                            b"combined-beta-service-token-0123456789abcdefg"
                        ).hexdigest(),
                        "session_signing_key_sha256": hashlib.sha256(
                            b"combined-beta-session-key-0123456789abcdefg"
                        ).hexdigest(),
                        "allowed_capabilities": NATIVE_ALLOWED_CAPABILITIES,
                    },
                    "ananta-langgraph-worker-1": {
                        "worker_url": "http://ai-agent-langgraph-worker:5000",
                        "registration_token": "langgraph-registration-token-0123456789abcdef",
                        "service_token_sha256": hashlib.sha256(
                            b"combined-langgraph-service-token-0123456789abcdef"
                        ).hexdigest(),
                        "session_signing_key_sha256": hashlib.sha256(
                            b"combined-langgraph-session-key-0123456789abcdef"
                        ).hexdigest(),
                        "allowed_capabilities": LANGGRAPH_ALLOWED_CAPABILITIES,
                    },
                },
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    registration_keyring.chmod(0o600)
    environment = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "INITIAL_ADMIN_PASSWORD": "combined-central-admin-password",
        "POSTGRES_PASSWORD": "combined-central-postgres-password",
        "TEMPORAL_POSTGRES_PASSWORD": "combined-temporal-postgres-password",
        "CORS_ORIGINS": "https://ananta.example.test",
        "ANANTA_WORKFLOW_AUTH_SIGNING_KEYRING_SECRET_FILE": "/tmp/combined-auth-signing",
        "ANANTA_WORKFLOW_AUTH_VERIFICATION_KEYRING_SECRET_FILE": "/tmp/combined-auth-verification",
        "ANANTA_WORKFLOW_DISPATCH_KEYRING_SECRET_FILE": "/tmp/combined-dispatch",
        "ANANTA_WORKFLOW_HUB_TOKEN_SECRET_FILE": "/tmp/combined-hub-token",
        "ANANTA_HUB_SESSION_SIGNING_KEY_SECRET_FILE": "/tmp/combined-hub-session",
        "ANANTA_WORKFLOW_WORKER_REGISTRATION_KEYRING_SECRET_FILE": str(registration_keyring),
        "ANANTA_WORKFLOW_WORKER_ALPHA_REGISTRATION_TOKEN_SECRET_FILE": "/tmp/combined-alpha-registration",
        "ANANTA_WORKFLOW_WORKER_BETA_REGISTRATION_TOKEN_SECRET_FILE": "/tmp/combined-beta-registration",
        "ANANTA_WORKFLOW_WORKER_ALPHA_SERVICE_TOKEN_SECRET_FILE": "/tmp/combined-alpha-service",
        "ANANTA_WORKFLOW_WORKER_BETA_SERVICE_TOKEN_SECRET_FILE": "/tmp/combined-beta-service",
        "ANANTA_WORKER_ALPHA_SESSION_SIGNING_KEY_SECRET_FILE": "/tmp/combined-alpha-session",
        "ANANTA_WORKER_BETA_SESSION_SIGNING_KEY_SECRET_FILE": "/tmp/combined-beta-session",
        "ANANTA_WORKFLOW_WORKER_LANGGRAPH_REGISTRATION_TOKEN_SECRET_FILE": "/tmp/combined-langgraph-registration",
        "ANANTA_WORKFLOW_WORKER_LANGGRAPH_SERVICE_TOKEN_SECRET_FILE": "/tmp/combined-langgraph-service",
        "ANANTA_WORKER_LANGGRAPH_SESSION_SIGNING_KEY_SECRET_FILE": "/tmp/combined-langgraph-session",
        "ANANTA_WORKFLOW_RUNTIME_SERVICE_KEYRING_SECRET_FILE": "/tmp/combined-runtime-keyring",
        "ANANTA_WORKFLOW_TEMPORAL_SERVICE_TOKEN_SECRET_FILE": "/tmp/combined-temporal-service",
    }
    files = [
        "compose.stack.full.yml",
        "compose.workflow-runtime.production.yml",
        "compose.native.production.yml",
        "compose.langgraph.production.yml",
        "compose.temporal.yml",
        "compose.temporal.production.yml",
    ]
    command = ["docker", "compose", "--env-file", "/dev/null"]
    for filename in files:
        command.extend(("-f", str(COMPOSE / filename)))
    command.extend(("--profile", "langgraph", "--profile", "temporal", "config", "--format", "json"))
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr

    rendered = json.loads(completed.stdout)
    services = rendered["services"]
    assert set(rendered["secrets"]) == EXPECTED_SECRETS
    hub = services["ai-agent-hub"]
    assert hub["environment"]["ANANTA_ORCHESTRATION_BACKEND"] == "temporal"
    assert hub["environment"]["ANANTA_WORKFLOW_BACKEND"] == "temporal"
    assert hub["environment"]["ANANTA_WORKFLOW_REQUIRE_REGISTERED_WORKER_AUTH"] == "1"
    assert hub["environment"]["CORS_ORIGINS"] == "https://ananta.example.test"
    assert hub["environment"]["ANANTA_QUICKSTART_ROLE"] == "hub"
    assert "workflow_runtime_service_keyring" in _secret_sources(hub)
    assert "workflow_worker_registration_keyring" in _secret_sources(hub)
    assert AUTH_VERIFICATION_KEYRING not in _secret_sources(hub)
    assert set(hub["networks"]) == {
        "default",
        "langgraph-runtime",
        "temporal-runtime",
        "workflow-ui-control",
        "workflow-worker-control",
    }
    assert not services["postgres"].get("ports")
    assert not services["redis"].get("ports")

    worker_names = (
        "ai-agent-alpha",
        "ai-agent-beta",
        "ai-agent-langgraph-worker",
        "ananta-temporal-worker",
    )
    for worker_name in worker_names:
        worker = services[worker_name]
        assert CENTRAL_ONLY_ENVIRONMENT.isdisjoint(worker.get("environment", {}))
        assert HUB_TOKEN not in _secret_sources(worker)
        assert "combined-central-admin-password" not in json.dumps(worker)
        assert "combined-central-postgres-password" not in json.dumps(worker)
    assert services["ai-agent-alpha"]["environment"]["ANANTA_QUICKSTART_ROLE"] == "worker"
    assert services["ai-agent-beta"]["environment"]["ANANTA_QUICKSTART_ROLE"] == "worker"
    assert services["ai-agent-langgraph-worker"]["environment"]["ANANTA_QUICKSTART_ROLE"] == "worker"

    assert services["angular-frontend"]["environment"] == {
        "ANANTA_QUICKSTART_MODE": "role",
        "ANANTA_QUICKSTART_ROLE": "frontend",
        "ANANTA_FRONTEND_DISABLE_HOST_CHECK": "0",
        "PLAYWRIGHT_BROWSERS_PATH": "/ms-playwright",
    }
    assert set(services["angular-frontend"]["networks"]) == {
        "workflow-ui-control"
    }
    assert not services["angular-frontend"].get("volumes")
    assert _secret_sources(services["ananta-temporal-worker"]) == {
        AUTH_VERIFICATION_KEYRING,
        "workflow_temporal_service_token",
    }
    assert services["ananta-temporal-worker"]["environment"]["ANANTA_WORKFLOW_SERVICE_ID"] == "ananta-temporal-worker"
    assert set(services["ai-agent-alpha"]["networks"]) == {"workflow-worker-control"}
    assert set(services["ai-agent-beta"]["networks"]) == {"workflow-worker-control"}
    assert set(services["ai-agent-langgraph-worker"]["networks"]) == {"langgraph-runtime"}
    assert set(services["ananta-temporal-worker"]["networks"]) == {"temporal-runtime"}
    assert not services["ai-agent-alpha"].get("ports")
    assert not services["ai-agent-beta"].get("ports")

    hub_workspace = _mount_at(hub, "/project-workspaces")
    assert hub_workspace["type"] == "bind"
    expected_worker_workspaces = {
        "ai-agent-alpha": "workflow_worker_alpha_workspace",
        "ai-agent-beta": "workflow_worker_beta_workspace",
        "ai-agent-langgraph-worker": "workflow_langgraph_worker_workspace",
    }
    workspace_sources: set[str] = set()
    for worker_name, expected_source in expected_worker_workspaces.items():
        worker = services[worker_name]
        assert all(mount["type"] != "bind" for mount in worker.get("volumes", ()))
        workspace = _mount_at(worker, "/project-workspaces")
        assert workspace["type"] == "volume"
        assert workspace["source"] == expected_source
        assert workspace["volume"]["nocopy"] is True
        workspace_sources.add(str(workspace["source"]))
    assert len(workspace_sources) == len(expected_worker_workspaces)
    assert str(hub_workspace["source"]) not in workspace_sources
    assert not services["ananta-temporal-worker"].get("volumes")
    assert "/project-workspaces" not in json.dumps(services["ananta-temporal-worker"])
    configured_workers = json.loads(registration_keyring.read_text(encoding="utf-8"))["workers"]
    assert configured_workers["ananta-worker-1"]["allowed_capabilities"] == (NATIVE_ALLOWED_CAPABILITIES)
    assert configured_workers["ananta-worker-2"]["allowed_capabilities"] == (NATIVE_ALLOWED_CAPABILITIES)
    assert configured_workers["ananta-langgraph-worker-1"]["allowed_capabilities"] == LANGGRAPH_ALLOWED_CAPABILITIES
