from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
STACK = ROOT / "docker" / "compose-next" / "compose.stack.full.yml"
SECURITY_OVERLAY = ROOT / "docker" / "compose-next" / "compose.workflow-runtime.production.yml"
PRODUCTION_OVERLAY = ROOT / "docker" / "compose-next" / "compose.temporal.production.yml"
TEMPORAL_OVERLAY = ROOT / "docker" / "compose-next" / "compose.temporal.yml"
PROBE_OVERLAY = ROOT / "docker" / "compose-next" / "compose.tests.temporal.yml"

AUTH_SIGNING_KEYRING = "workflow_runtime_auth_signing_keyring"
AUTH_VERIFICATION_KEYRING = "workflow_runtime_auth_verification_keyring"
DISPATCH_KEYRING = "workflow_runtime_dispatch_keyring"
HUB_TOKEN = "workflow_hub_service_token"
RUNTIME_SERVICE_KEYRING = "workflow_runtime_service_keyring"
TEMPORAL_SERVICE_TOKEN = "workflow_temporal_service_token"


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


def test_production_overlay_uses_external_file_secrets_only() -> None:
    overlay = _load(PRODUCTION_OVERLAY)

    assert set(overlay["secrets"]) == {
        RUNTIME_SERVICE_KEYRING,
        TEMPORAL_SERVICE_TOKEN,
    }
    for secret in overlay["secrets"].values():
        reference = str(secret["file"])
        assert reference.startswith("${ANANTA_WORKFLOW_")
        assert ":?Error:" in reference
    rendered = PRODUCTION_OVERLAY.read_text(encoding="utf-8")
    assert "AGENT_TOKEN:" not in rendered
    assert "keys:" not in rendered
    assert "active_key_id:" not in rendered


def test_hub_owns_dispatch_key_and_temporal_control_connection() -> None:
    common_hub = _load(SECURITY_OVERLAY)["services"]["ai-agent-hub"]
    hub = _load(PRODUCTION_OVERLAY)["services"]["ai-agent-hub"]
    environment = hub["environment"]

    assert _secret_sources(common_hub) == {
        AUTH_SIGNING_KEYRING,
        DISPATCH_KEYRING,
        HUB_TOKEN,
        "workflow_hub_session_signing_key",
        "workflow_worker_registration_keyring",
    }
    assert _secret_sources(hub) == {RUNTIME_SERVICE_KEYRING}
    _assert_read_only_secret_bindings(hub)
    assert common_hub["environment"]["AGENT_TOKEN_FILE"] == f"/run/secrets/{HUB_TOKEN}"
    assert common_hub["environment"]["ANANTA_WORKFLOW_AUTH_SIGNING_KEYRING_FILE"] == (
        f"/run/secrets/{AUTH_SIGNING_KEYRING}"
    )
    assert common_hub["environment"]["ANANTA_WORKFLOW_DISPATCH_KEYRING_FILE"] == (f"/run/secrets/{DISPATCH_KEYRING}")
    assert environment["ANANTA_WORKFLOW_RUNTIME_SERVICE_KEYRING_FILE"] == (f"/run/secrets/{RUNTIME_SERVICE_KEYRING}")
    assert environment["ANANTA_ORCHESTRATION_BACKEND"] == "temporal"
    assert environment["ANANTA_TEMPORAL_ADDRESS"] == "temporal:7233"
    assert set(hub["networks"]) == {"default", "temporal-runtime"}


def test_temporal_worker_gets_public_verification_and_scoped_service_credential() -> None:
    worker = _load(PRODUCTION_OVERLAY)["services"]["ananta-temporal-worker"]
    environment = worker["environment"]

    assert _secret_sources(worker) == {
        AUTH_VERIFICATION_KEYRING,
        TEMPORAL_SERVICE_TOKEN,
    }
    _assert_read_only_secret_bindings(worker)
    assert DISPATCH_KEYRING not in _secret_sources(worker)
    assert AUTH_SIGNING_KEYRING not in _secret_sources(worker)
    assert environment["ANANTA_WORKFLOW_AUTH_VERIFICATION_KEYRING_FILE"] == (
        f"/run/secrets/{AUTH_VERIFICATION_KEYRING}"
    )
    assert environment["ANANTA_TEMPORAL_HUB_TOKEN_FILE"] == (f"/run/secrets/{TEMPORAL_SERVICE_TOKEN}")
    assert environment["ANANTA_WORKFLOW_SERVICE_ID"] == "ananta-temporal-worker"
    assert environment["ANANTA_TEMPORAL_HUB_URL"] == "http://ai-agent-hub:5000"
    assert HUB_TOKEN not in _secret_sources(worker)
    assert worker["depends_on"]["ai-agent-hub"]["condition"] == "service_healthy"
    assert worker["volumes"] == []
    assert "ANANTA_WORKSPACE_ROOT" not in environment


def test_probe_overlays_remain_secret_free_and_side_effect_free() -> None:
    temporal = _load(TEMPORAL_OVERLAY)
    probe = _load(PROBE_OVERLAY)

    assert "secrets" not in temporal
    assert "secrets" not in probe
    smoke = probe["services"]["temporal-smoke"]
    assert "ANANTA_TEMPORAL_HUB_URL" not in smoke.get("environment", {})
    assert "ANANTA_TEMPORAL_HUB_TOKEN_FILE" not in smoke.get("environment", {})
    assert smoke["entrypoint"] == ["python", "-m", "worker.temporal.smoke"]


def test_temporal_control_and_ui_ports_are_never_public_by_default() -> None:
    temporal = _load(TEMPORAL_OVERLAY)["services"]
    production = _load(PRODUCTION_OVERLAY)["services"]

    assert temporal["temporal"]["ports"] == ["${TEMPORAL_BIND_ADDRESS:-127.0.0.1}:${TEMPORAL_GRPC_PORT:-7233}:7233"]
    assert temporal["temporal-ui"]["ports"] == ["${TEMPORAL_UI_BIND_ADDRESS:-127.0.0.1}:${TEMPORAL_UI_PORT:-8233}:8080"]
    assert production["temporal"]["ports"] == ["127.0.0.1:${TEMPORAL_GRPC_PORT:-7233}:7233"]
    assert production["temporal-ui"]["ports"] == ["127.0.0.1:${TEMPORAL_UI_PORT:-8233}:8080"]


def test_temporal_production_merged_model_uses_only_scoped_runtime_credential() -> None:
    if shutil.which("docker") is None:
        pytest.skip("docker CLI is not installed")
    environment = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "INITIAL_ADMIN_PASSWORD": "temporal-central-admin-password",
        "POSTGRES_PASSWORD": "temporal-central-postgres-password",
        "TEMPORAL_POSTGRES_PASSWORD": "temporal-database-password",
        "CORS_ORIGINS": "https://ananta.example.test",
        "ANANTA_WORKFLOW_AUTH_SIGNING_KEYRING_SECRET_FILE": "/tmp/workflow-auth-signing.json",
        "ANANTA_WORKFLOW_AUTH_VERIFICATION_KEYRING_SECRET_FILE": "/tmp/workflow-auth-verification.json",
        "ANANTA_WORKFLOW_DISPATCH_KEYRING_SECRET_FILE": "/tmp/workflow-dispatch.json",
        "ANANTA_WORKFLOW_HUB_TOKEN_SECRET_FILE": "/tmp/workflow-hub-token",
        "ANANTA_HUB_SESSION_SIGNING_KEY_SECRET_FILE": "/tmp/workflow-hub-session",
        "ANANTA_WORKFLOW_WORKER_REGISTRATION_KEYRING_SECRET_FILE": "/tmp/workflow-registration-keyring",
        "ANANTA_WORKFLOW_WORKER_ALPHA_REGISTRATION_TOKEN_SECRET_FILE": "/tmp/workflow-alpha-registration",
        "ANANTA_WORKFLOW_WORKER_BETA_REGISTRATION_TOKEN_SECRET_FILE": "/tmp/workflow-beta-registration",
        "ANANTA_WORKFLOW_WORKER_ALPHA_SERVICE_TOKEN_SECRET_FILE": "/tmp/workflow-alpha-service",
        "ANANTA_WORKFLOW_WORKER_BETA_SERVICE_TOKEN_SECRET_FILE": "/tmp/workflow-beta-service",
        "ANANTA_WORKER_ALPHA_SESSION_SIGNING_KEY_SECRET_FILE": "/tmp/workflow-alpha-session",
        "ANANTA_WORKER_BETA_SESSION_SIGNING_KEY_SECRET_FILE": "/tmp/workflow-beta-session",
        "ANANTA_WORKFLOW_RUNTIME_SERVICE_KEYRING_SECRET_FILE": "/tmp/workflow-runtime-service-keyring",
        "ANANTA_WORKFLOW_TEMPORAL_SERVICE_TOKEN_SECRET_FILE": "/tmp/workflow-temporal-service-token",
    }
    completed = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            "/dev/null",
            "-f",
            str(STACK),
            "-f",
            str(SECURITY_OVERLAY),
            "-f",
            str(TEMPORAL_OVERLAY),
            "-f",
            str(PRODUCTION_OVERLAY),
            "--profile",
            "temporal",
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
    hub = rendered["services"]["ai-agent-hub"]
    worker = rendered["services"]["ananta-temporal-worker"]
    assert RUNTIME_SERVICE_KEYRING in _secret_sources(hub)
    assert HUB_TOKEN not in _secret_sources(worker)
    assert _secret_sources(worker) == {
        AUTH_VERIFICATION_KEYRING,
        TEMPORAL_SERVICE_TOKEN,
    }
    assert worker["environment"]["ANANTA_WORKFLOW_SERVICE_ID"] == ("ananta-temporal-worker")
    assert "temporal-central-admin-password" not in json.dumps(worker)
    assert "temporal-central-postgres-password" not in json.dumps(worker)
    assert not worker.get("volumes")
    assert "/project-workspaces" not in json.dumps(worker)
