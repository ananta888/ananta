from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from agent.services.workflow_worker_service_auth import load_worker_registration_keyring
from ananta_contracts.file_credentials import read_file_managed_bytes
from worker.runtime.workflow_adapter_worker_profile import (
    load_workflow_adapter_worker_profile,
)

ROOT = Path(__file__).resolve().parents[3]
STACK = ROOT / "docker/compose-next/compose.stack.full.yml"
SECURITY_OVERLAY = ROOT / "docker/compose-next/compose.workflow-runtime.production.yml"
OVERLAY = ROOT / "docker/compose-next/compose.native.production.yml"
PROFILE = ROOT / "config/workflow_runtime/native_worker_profile.v1.json"

AUTH_SIGNING_KEYRING = "workflow_runtime_auth_signing_keyring"
AUTH_VERIFICATION_KEYRING = "workflow_runtime_auth_verification_keyring"
DISPATCH_KEYRING = "workflow_runtime_dispatch_keyring"
HUB_TOKEN = "workflow_hub_service_token"
HUB_SESSION_KEY = "workflow_hub_session_signing_key"
REGISTRATION_KEYRING = "workflow_worker_registration_keyring"
ALPHA_REGISTRATION_TOKEN = "workflow_worker_alpha_registration_token"
BETA_REGISTRATION_TOKEN = "workflow_worker_beta_registration_token"
ALPHA_SERVICE_TOKEN = "workflow_worker_alpha_service_token"
BETA_SERVICE_TOKEN = "workflow_worker_beta_service_token"
ALPHA_SESSION_KEY = "workflow_worker_alpha_session_signing_key"
BETA_SESSION_KEY = "workflow_worker_beta_session_signing_key"

SECRET_FILE_ENVIRONMENTS = {
    AUTH_SIGNING_KEYRING: "ANANTA_WORKFLOW_AUTH_SIGNING_KEYRING_SECRET_FILE",
    AUTH_VERIFICATION_KEYRING: "ANANTA_WORKFLOW_AUTH_VERIFICATION_KEYRING_SECRET_FILE",
    DISPATCH_KEYRING: "ANANTA_WORKFLOW_DISPATCH_KEYRING_SECRET_FILE",
    HUB_TOKEN: "ANANTA_WORKFLOW_HUB_TOKEN_SECRET_FILE",
    HUB_SESSION_KEY: "ANANTA_HUB_SESSION_SIGNING_KEY_SECRET_FILE",
    REGISTRATION_KEYRING: "ANANTA_WORKFLOW_WORKER_REGISTRATION_KEYRING_SECRET_FILE",
    ALPHA_REGISTRATION_TOKEN: "ANANTA_WORKFLOW_WORKER_ALPHA_REGISTRATION_TOKEN_SECRET_FILE",
    BETA_REGISTRATION_TOKEN: "ANANTA_WORKFLOW_WORKER_BETA_REGISTRATION_TOKEN_SECRET_FILE",
    ALPHA_SERVICE_TOKEN: "ANANTA_WORKFLOW_WORKER_ALPHA_SERVICE_TOKEN_SECRET_FILE",
    BETA_SERVICE_TOKEN: "ANANTA_WORKFLOW_WORKER_BETA_SERVICE_TOKEN_SECRET_FILE",
    ALPHA_SESSION_KEY: "ANANTA_WORKER_ALPHA_SESSION_SIGNING_KEY_SECRET_FILE",
    BETA_SESSION_KEY: "ANANTA_WORKER_BETA_SESSION_SIGNING_KEY_SECRET_FILE",
}
WORKER_MATRIX = {
    "ai-agent-alpha": {
        "registration": ALPHA_REGISTRATION_TOKEN,
        "service": ALPHA_SERVICE_TOKEN,
        "session": ALPHA_SESSION_KEY,
    },
    "ai-agent-beta": {
        "registration": BETA_REGISTRATION_TOKEN,
        "service": BETA_SERVICE_TOKEN,
        "session": BETA_SESSION_KEY,
    },
}
WORKER_WORKSPACE_VOLUMES = {
    "ai-agent-alpha": "workflow_worker_alpha_workspace",
    "ai-agent-beta": "workflow_worker_beta_workspace",
}
CENTRAL_ONLY_ENVIRONMENT = {
    "DATABASE_URL",
    "REDIS_URL",
    "INITIAL_ADMIN_USER",
    "INITIAL_ADMIN_PASSWORD",
    "SECRET_KEY",
}
DOMAIN_SUPPLEMENT_SOURCE_MAX_BYTES_ENV = (
    "ANANTA_CODECOMPASS_DOMAIN_SUPPLEMENT_SOURCE_MAX_BYTES"
)
DOMAIN_SUPPLEMENT_SOURCE_MAX_BYTES_TEST_VALUE = "3221225472"
NATIVE_ALLOWED_CAPABILITIES = [
    "planning",
    "analysis",
    "research",
    "source_analysis",
    "coding",
    "implementation",
    "review",
    "testing",
    "verification",
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


class _ComposeLoader(yaml.SafeLoader):
    """Parse Compose control tags while preserving their payload for assertions."""


def _construct_compose_tag(
    loader: _ComposeLoader,
    node: yaml.Node,
) -> Any:
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node, deep=True)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node, deep=True)
    return loader.construct_scalar(node)


_ComposeLoader.add_constructor("!override", _construct_compose_tag)
_ComposeLoader.add_constructor("!reset", _construct_compose_tag)


def _load(path: Path) -> dict[str, Any]:
    value = yaml.load(path.read_text(encoding="utf-8"), Loader=_ComposeLoader)
    assert isinstance(value, dict)
    return value


def _secret_bindings(service: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for value in service.get("secrets", ()):
        binding = value if isinstance(value, dict) else {"source": value, "target": value}
        result[str(binding["source"])] = binding
    return result


def _secret_sources(service: dict[str, Any]) -> set[str]:
    return set(_secret_bindings(service))


def _mount_at(service: dict[str, Any], target: str) -> dict[str, Any]:
    matches = [
        mount
        for mount in service.get("volumes", ())
        if isinstance(mount, dict) and mount.get("target") == target
    ]
    assert len(matches) == 1
    return matches[0]


def _required_file_reference(environment_name: str) -> str:
    return "${" + environment_name + ":?Error: "


def _docker_environment(secret_paths: dict[str, Path]) -> dict[str, str]:
    environment = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "INITIAL_ADMIN_PASSWORD": "native-central-admin-password",
        "POSTGRES_PASSWORD": "native-central-postgres-password",
        "CORS_ORIGINS": "https://ananta.example.test",
    }
    environment.update({SECRET_FILE_ENVIRONMENTS[name]: str(path) for name, path in secret_paths.items()})
    return environment


def _temporary_secret_files(tmp_path: Path) -> tuple[dict[str, Path], dict[str, bytes]]:
    paths: dict[str, Path] = {}
    payloads = {
        name: f"native-secret-{index:02d}-".encode("ascii") + b"x" * 48
        for index, name in enumerate(SECRET_FILE_ENVIRONMENTS, start=1)
    }
    payloads[REGISTRATION_KEYRING] = json.dumps(
        {
            "schema": "ananta.workflow-worker-registration-keyring.v1",
            "workers": {
                "ananta-worker-1": {
                    "worker_url": "http://ai-agent-alpha:5000",
                    "registration_token": payloads[ALPHA_REGISTRATION_TOKEN].decode("ascii"),
                    "service_token_sha256": hashlib.sha256(
                        payloads[ALPHA_SERVICE_TOKEN]
                    ).hexdigest(),
                    "session_signing_key_sha256": hashlib.sha256(
                        payloads[ALPHA_SESSION_KEY]
                    ).hexdigest(),
                    "allowed_capabilities": NATIVE_ALLOWED_CAPABILITIES,
                },
                "ananta-worker-2": {
                    "worker_url": "http://ai-agent-beta:5000",
                    "registration_token": payloads[BETA_REGISTRATION_TOKEN].decode("ascii"),
                    "service_token_sha256": hashlib.sha256(
                        payloads[BETA_SERVICE_TOKEN]
                    ).hexdigest(),
                    "session_signing_key_sha256": hashlib.sha256(
                        payloads[BETA_SESSION_KEY]
                    ).hexdigest(),
                    "allowed_capabilities": NATIVE_ALLOWED_CAPABILITIES,
                },
            },
        },
        sort_keys=True,
    ).encode("utf-8")
    for name in SECRET_FILE_ENVIRONMENTS:
        path = tmp_path / name
        payload = payloads[name]
        path.write_bytes(payload)
        path.chmod(0o600)
        paths[name] = path
        payloads[name] = payload
    return paths, payloads


def _render_compose(environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
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
            str(OVERLAY),
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


def test_native_production_overlay_uses_required_read_only_external_secrets() -> None:
    overlay = _load(SECURITY_OVERLAY)
    assert set(overlay["secrets"]) == set(SECRET_FILE_ENVIRONMENTS)

    raw_overlay = SECURITY_OVERLAY.read_text(encoding="utf-8")
    assert raw_overlay.count("environment: !override") == 4
    assert "AGENT_TOKEN:" not in raw_overlay
    assert "REGISTRATION_TOKEN:" not in raw_overlay
    assert "active_key_id:" not in raw_overlay

    for secret_name, environment_name in SECRET_FILE_ENVIRONMENTS.items():
        declaration = overlay["secrets"][secret_name]
        assert set(declaration) == {"file"}
        assert declaration["file"].startswith(_required_file_reference(environment_name))

    for service in overlay["services"].values():
        for binding in _secret_bindings(service).values():
            assert binding["source"] == binding["target"]
            assert int(binding["mode"]) & 0o222 == 0


def test_native_hub_and_workers_have_disjoint_least_privilege_secret_matrix() -> None:
    services = _load(SECURITY_OVERLAY)["services"]
    hub = services["ai-agent-hub"]
    hub_environment = hub["environment"]
    assert _secret_sources(hub) == {
        AUTH_SIGNING_KEYRING,
        DISPATCH_KEYRING,
        HUB_TOKEN,
        HUB_SESSION_KEY,
        REGISTRATION_KEYRING,
    }
    assert hub_environment["SECRET_KEY_FILE"] == f"/run/secrets/{HUB_SESSION_KEY}"
    assert hub_environment["AGENT_TOKEN_FILE"] == f"/run/secrets/{HUB_TOKEN}"
    assert hub_environment["ANANTA_WORKFLOW_REQUIRE_REGISTERED_WORKER_AUTH"] == "1"
    assert hub_environment["CORS_ORIGINS"] == (
        "${CORS_ORIGINS:?Error: CORS_ORIGINS must be an explicit production origin allowlist}"
    )
    assert hub_environment["ANANTA_WORKFLOW_WORKER_REGISTRATION_KEYRING_FILE"] == (
        f"/run/secrets/{REGISTRATION_KEYRING}"
    )
    assert "SECRET_KEY" not in hub_environment
    assert AUTH_VERIFICATION_KEYRING not in _secret_sources(hub)

    all_worker_private_secrets = {value for worker in WORKER_MATRIX.values() for value in worker.values()}
    for service_name, expected in WORKER_MATRIX.items():
        service = services[service_name]
        environment = service["environment"]
        assert _secret_sources(service) == {
            AUTH_VERIFICATION_KEYRING,
            expected["registration"],
            expected["service"],
            expected["session"],
        }
        assert CENTRAL_ONLY_ENVIRONMENT.isdisjoint(environment)
        assert environment["DISABLE_INITIAL_ADMIN"] == "1"
        assert environment["AGENT_TOKEN_PERSISTENCE"] == "0"
        assert environment["SECRET_KEY_FILE"] == f"/run/secrets/{expected['session']}"
        assert environment["REGISTRATION_TOKEN_FILE"] == (f"/run/secrets/{expected['registration']}")
        assert environment["AGENT_TOKEN_FILE"] == f"/run/secrets/{expected['service']}"
        assert environment["ANANTA_WORKFLOW_HUB_TOKEN_FILE"] == (f"/run/secrets/{expected['service']}")
        assert environment["ANANTA_WORKFLOW_HUB_URL"] == "http://ai-agent-hub:5000"
        assert HUB_TOKEN not in _secret_sources(service)
        assert AUTH_SIGNING_KEYRING not in _secret_sources(service)
        assert DISPATCH_KEYRING not in _secret_sources(service)
        assert REGISTRATION_KEYRING not in _secret_sources(service)
        assert not (_secret_sources(service) & (all_worker_private_secrets - set(expected.values())))

    assert services["angular-frontend"]["environment"] == {
        "ANANTA_QUICKSTART_MODE": "role",
        "ANANTA_QUICKSTART_ROLE": "frontend",
        "ANANTA_FRONTEND_DISABLE_HOST_CHECK": "0",
        "PLAYWRIGHT_BROWSERS_PATH": "/ms-playwright",
    }
    assert services["angular-frontend"]["volumes"] == []
    assert services["angular-frontend"]["networks"] == ["workflow-ui-control"]


def test_native_production_workers_use_identity_local_named_workspaces() -> None:
    overlay = _load(SECURITY_OVERLAY)
    services = overlay["services"]
    assert set(WORKER_WORKSPACE_VOLUMES.values()) <= set(overlay["volumes"])

    for service_name, volume_name in WORKER_WORKSPACE_VOLUMES.items():
        workspace = _mount_at(services[service_name], "/project-workspaces")
        assert workspace == {
            "type": "volume",
            "source": volume_name,
            "target": "/project-workspaces",
            "volume": {"nocopy": True},
        }
        assert all(
            not isinstance(mount, str) or "project-workspaces" not in mount
            for mount in services[service_name]["volumes"]
        )

    base_worker = _load(ROOT / "docker/compose-next/compose.base.yml")["services"][
        "ai-agent-worker-base"
    ]
    assert "../../project-workspaces:/project-workspaces:rw" in base_worker["volumes"]


def test_native_runtime_overlay_only_selects_the_local_hub_backends() -> None:
    native = _load(OVERLAY)
    assert native == {
        "services": {
            "ai-agent-hub": {
                "environment": {
                    "ANANTA_ORCHESTRATION_BACKEND": "local",
                    "ANANTA_WORKFLOW_BACKEND": "local",
                }
            }
        }
    }


def test_native_worker_profile_is_typed_and_matches_runtime_capabilities() -> None:
    profile = load_workflow_adapter_worker_profile(str(PROFILE))
    native = profile.worker_runtime.native_graph
    assert native is not None and native.enabled
    assert {
        "coding",
        "planning_research",
        "testing",
        "verification",
    } <= set(native.allowed_task_types)
    assert {
        "approval",
        "bounded_parallel",
        "checkpoint",
        "deterministic_merge",
        "resume",
        "retrieval",
        "source_analysis",
        "structured_output",
        "tool_calling",
    } <= set(native.capabilities)
    decoded = json.loads(PROFILE.read_text(encoding="utf-8"))
    assert "secret" not in json.dumps(decoded).lower()


def test_native_production_merged_model_contains_no_worker_or_angular_admin_secrets(
    tmp_path: Path,
) -> None:
    if shutil.which("docker") is None:
        pytest.skip("docker CLI is not installed")
    secret_paths, secret_payloads = _temporary_secret_files(tmp_path)
    environment = _docker_environment(secret_paths)
    environment[DOMAIN_SUPPLEMENT_SOURCE_MAX_BYTES_ENV] = (
        DOMAIN_SUPPLEMENT_SOURCE_MAX_BYTES_TEST_VALUE
    )
    completed = _render_compose(environment)
    assert completed.returncode == 0, completed.stderr

    rendered = json.loads(completed.stdout)
    services = rendered["services"]
    hub_environment = services["ai-agent-hub"]["environment"]
    assert "SECRET_KEY" not in hub_environment
    assert hub_environment["SECRET_KEY_FILE"] == f"/run/secrets/{HUB_SESSION_KEY}"
    assert "native-central-admin-password" in hub_environment["INITIAL_ADMIN_PASSWORD"]
    assert "native-central-postgres-password" in hub_environment["DATABASE_URL"]
    assert hub_environment["CORS_ORIGINS"] == "https://ananta.example.test"
    assert hub_environment["ANANTA_QUICKSTART_ROLE"] == "hub"
    assert set(services["ai-agent-hub"]["networks"]) == {
        "default",
        "workflow-ui-control",
        "workflow-worker-control",
    }
    assert not services["postgres"].get("ports")
    assert not services["redis"].get("ports")

    for service_name in WORKER_MATRIX:
        worker = services[service_name]
        environment = worker["environment"]
        assert CENTRAL_ONLY_ENVIRONMENT.isdisjoint(environment)
        assert set(worker.get("depends_on", ())) == {"ai-agent-hub"}
        assert "native-central-admin-password" not in json.dumps(worker)
        assert "native-central-postgres-password" not in json.dumps(worker)
        assert HUB_TOKEN not in _secret_sources(worker)
        assert environment["ANANTA_QUICKSTART_ROLE"] == "worker"
        assert environment[DOMAIN_SUPPLEMENT_SOURCE_MAX_BYTES_ENV] == (
            DOMAIN_SUPPLEMENT_SOURCE_MAX_BYTES_TEST_VALUE
        )
        assert set(worker["networks"]) == {"workflow-worker-control"}
        assert not worker.get("ports")

    hub_workspace = _mount_at(services["ai-agent-hub"], "/project-workspaces")
    assert hub_workspace["type"] == "bind"
    worker_workspace_sources: set[str] = set()
    for service_name, volume_name in WORKER_WORKSPACE_VOLUMES.items():
        worker = services[service_name]
        assert all(mount["type"] != "bind" for mount in worker.get("volumes", ()))
        workspace = _mount_at(worker, "/project-workspaces")
        assert workspace["type"] == "volume"
        assert workspace["source"] == volume_name
        assert workspace["volume"]["nocopy"] is True
        worker_workspace_sources.add(str(workspace["source"]))
    assert len(worker_workspace_sources) == len(WORKER_WORKSPACE_VOLUMES)
    assert str(hub_workspace["source"]) not in worker_workspace_sources

    angular = services["angular-frontend"]
    assert angular["environment"] == {
        "ANANTA_QUICKSTART_MODE": "role",
        "ANANTA_QUICKSTART_ROLE": "frontend",
        "ANANTA_FRONTEND_DISABLE_HOST_CHECK": "0",
        "PLAYWRIGHT_BROWSERS_PATH": "/ms-playwright",
    }
    assert set(angular["networks"]) == {"workflow-ui-control"}
    assert not angular.get("volumes")
    assert "native-central-admin-password" not in json.dumps(angular)

    for name, path in secret_paths.items():
        assert rendered["secrets"][name]["file"] == str(path)
        assert path.stat().st_mode & 0o777 == 0o600
        assert path.stat().st_uid in {0, os.geteuid()}
        assert (
            read_file_managed_bytes(
                    str(path),
                    description=f"test {name}",
                    max_bytes=4096,
            )
            == secret_payloads[name]
        )
        assert secret_payloads[name].decode("ascii") not in completed.stdout

    registration_keyring = json.loads(secret_paths[REGISTRATION_KEYRING].read_text(encoding="utf-8"))
    assert {
        worker_id: entry["allowed_capabilities"] for worker_id, entry in registration_keyring["workers"].items()
    } == {
        "ananta-worker-1": NATIVE_ALLOWED_CAPABILITIES,
        "ananta-worker-2": NATIVE_ALLOWED_CAPABILITIES,
    }
    loaded_keyring = load_worker_registration_keyring(
        {
            "ANANTA_WORKFLOW_WORKER_REGISTRATION_KEYRING_FILE": str(
                secret_paths[REGISTRATION_KEYRING]
            )
        }
    )
    assert {
        worker_id: set(credential.allowed_capabilities)
        for worker_id, credential in loaded_keyring.items()
    } == {
        "ananta-worker-1": set(NATIVE_ALLOWED_CAPABILITIES),
        "ananta-worker-2": set(NATIVE_ALLOWED_CAPABILITIES),
    }


def test_native_production_render_fails_when_a_required_worker_secret_is_missing(
    tmp_path: Path,
) -> None:
    if shutil.which("docker") is None:
        pytest.skip("docker CLI is not installed")
    secret_paths, _ = _temporary_secret_files(tmp_path)
    environment = _docker_environment(secret_paths)
    environment.pop(SECRET_FILE_ENVIRONMENTS[ALPHA_SERVICE_TOKEN])

    completed = _render_compose(environment)

    assert completed.returncode != 0
    assert "alpha Worker service-token secret file must be set" in completed.stderr
    assert "native-central-admin-password" not in completed.stderr


def test_native_production_render_rejects_implicit_wildcard_cors(tmp_path: Path) -> None:
    if shutil.which("docker") is None:
        pytest.skip("docker CLI is not installed")
    secret_paths, _ = _temporary_secret_files(tmp_path)
    environment = _docker_environment(secret_paths)
    environment.pop("CORS_ORIGINS")

    completed = _render_compose(environment)

    assert completed.returncode != 0
    assert "CORS_ORIGINS must be an explicit production origin allowlist" in (completed.stderr)
