from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from agent.services.codecompass_vector_runtime_service import (
    build_default_codecompass_vector_runtime_resolver,
)
from agent.services.vector_store_rollout_service import (
    InMemoryVectorStoreRolloutStore,
    VectorStoreRolloutService,
)
from agent.services.wiki_vector_runtime_service import (
    build_default_wiki_vector_runtime_resolver,
)

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "docker/compose-next/compose.qdrant.yml"
WORKER_OVERLAY = ROOT / "docker/compose-next/compose.qdrant-workers.yml"
DEV_AUTH_OVERLAY = (
    ROOT
    / "docker/compose-next/compose.workflow-runtime.dev-auth.yml"
)
CODECOMPASS_HUB_READ_OVERLAY = ROOT / "docker/compose-next/compose.qdrant-hub-read.yml"
WIKI_HUB_READ_OVERLAY = ROOT / "docker/compose-next/compose.qdrant-wiki-hub-read.yml"
QUICKSTART_COMPOSE = ROOT / "docker/compose-next/compose.stack.quickstart.yml"
QUALITY_WORKFLOW = ROOT / ".github/workflows/quality-and-docs.yml"
EXPECTED_IMAGE = "qdrant/qdrant:v1.18.2@sha256:75eab8c4ba42096724fdcfde8b4de0b5713d529dde32f285a1f86fdcb2c9e50c"
DOCKERIGNORE = ROOT / ".dockerignore"
QUICKSTART_DOCKERFILE = (
    ROOT / "docker/compose-next/Dockerfile.quickstart-no-ollama"
)


class _TaskSubmissionPort:
    def submit(self, **_kwargs: object) -> dict[str, object]:
        return {}


class _InputPublisherPort:
    def publish(self, **_kwargs: object) -> dict[str, object]:
        return {}


def _render_wiki_qdrant_quickstart() -> dict[str, object]:
    if shutil.which("docker") is None:
        pytest.skip("docker CLI is not installed")
    environment = os.environ.copy()
    environment.update(
        {
            "INITIAL_ADMIN_PASSWORD": "compose-contract-password",
            "ANANTA_WIKI_VECTOR_WORKSPACE_ID": "wiki-workspace",
            "ANANTA_WIKI_VECTOR_SOURCE_ID": "wiki-source",
            "ANANTA_WIKI_VECTOR_PROFILE_NAME": "wiki-profile",
            "ANANTA_WIKI_VECTOR_RETRIEVAL_CACHE_STATE": "cache-v2",
            "ANANTA_WIKI_VECTOR_MANIFEST_HASH": "manifest-v2",
            # These values prove that a Wiki-only overlay does not interpolate
            # unrelated CodeCompass settings from the caller environment.
            "CODECOMPASS_VECTOR_ENABLED": "1",
            "ANANTA_CODECOMPASS_VECTOR_WORKSPACE_ID": "must-not-leak",
            "ANANTA_CODECOMPASS_VECTOR_REPOSITORY_ID": "must-not-leak",
        }
    )
    completed = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(QUICKSTART_COMPOSE),
            "-f",
            str(DEV_AUTH_OVERLAY),
            "-f",
            str(COMPOSE),
            "-f",
            str(WORKER_OVERLAY),
            "-f",
            str(WIKI_HUB_READ_OVERLAY),
            "--profile",
            "qdrant",
            "config",
        ],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    return yaml.safe_load(completed.stdout)


def test_qdrant_compose_profile_is_pinned_private_and_persistent() -> None:
    payload = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    service = payload["services"]["qdrant"]

    assert service["profiles"] == ["qdrant"]
    assert service["image"] == EXPECTED_IMAGE
    assert service["restart"] == "unless-stopped"
    assert service["read_only"] is True
    assert service["cap_drop"] == ["ALL"]
    assert service["cap_add"] == ["DAC_READ_SEARCH"]
    assert service["pids_limit"] == 256
    assert service["healthcheck"]["test"][0] == "CMD-SHELL"
    assert "/healthz" in service["healthcheck"]["test"][1]
    assert "openssl s_client" in service["healthcheck"]["test"][1]
    assert "-verify_hostname localhost" in service["healthcheck"]["test"][1]
    rendered_entrypoint = "\n".join(service["entrypoint"])
    assert "$(cat /run/secrets/qdrant-api-key)" in rendered_entrypoint
    assert "$$(cat /run/secrets/qdrant-api-key)" not in rendered_entrypoint
    assert "qdrant_api_key_secret_unreadable" in rendered_entrypoint
    assert "qdrant_tls_material_missing" in rendered_entrypoint
    assert "qdrant-data:/qdrant/storage" in service["volumes"]
    assert "qdrant-snapshots:/qdrant/snapshots" in service["volumes"]
    assert service["environment"]["QDRANT__STORAGE__SNAPSHOTS_PATH"] == "/qdrant/snapshots"
    assert service["environment"]["QDRANT__SERVICE__ENABLE_TLS"] == "true"
    assert service["environment"]["QDRANT__TLS__CERT"].endswith("qdrant-tls-cert.pem")
    assert service["environment"]["QDRANT__TLS__KEY"].endswith("qdrant-tls-key.pem")
    assert {item["source"] if isinstance(item, dict) else item for item in service["secrets"]} == {
        "qdrant-api-key",
        "qdrant-tls-ca",
        "qdrant-tls-cert",
        "qdrant-tls-key",
    }
    assert payload["networks"]["qdrant-worker"]["internal"] is True
    assert set(service["networks"]) == {"qdrant-worker", "qdrant-edge"}
    assert "qdrant-edge" in payload["networks"]
    assert all(
        "qdrant-edge" not in (other.get("networks") or ())
        for name, other in payload["services"].items()
        if name != "qdrant"
    )


def test_qdrant_compose_ports_default_to_loopback_and_secret_is_not_environment() -> None:
    text = COMPOSE.read_text(encoding="utf-8")
    payload = yaml.safe_load(text)
    service = payload["services"]["qdrant"]

    assert service["ports"] == ["${ANANTA_QDRANT_BIND_IP:-127.0.0.1}:${ANANTA_QDRANT_REST_PORT:-6333}:6333/tcp"]
    grpc_overlay = yaml.safe_load((COMPOSE.parent / "compose.qdrant-grpc-host.yml").read_text(encoding="utf-8"))
    assert "127.0.0.1" in grpc_overlay["services"]["qdrant"]["ports"][0]
    assert ":6334/tcp" in grpc_overlay["services"]["qdrant"]["ports"][0]
    assert "QDRANT__SERVICE__API_KEY:" not in text
    assert "/run/secrets/qdrant-api-key" in service["entrypoint"][2]
    assert payload["secrets"]["qdrant-api-key"]["file"].startswith("${ANANTA_QDRANT_API_KEY_FILE")
    assert payload["secrets"]["qdrant-tls-ca"]["file"].startswith("${ANANTA_QDRANT_TLS_CA_FILE")
    assert payload["secrets"]["qdrant-tls-cert"]["file"].startswith("${ANANTA_QDRANT_TLS_CERT_FILE")
    assert payload["secrets"]["qdrant-tls-key"]["file"].startswith("${ANANTA_QDRANT_TLS_KEY_FILE")


def test_qdrant_workers_build_the_optional_client_runtime_only_in_profile() -> None:
    payload = yaml.safe_load(WORKER_OVERLAY.read_text(encoding="utf-8"))
    assert payload["secrets"]["vector-index-task-signing-keyring"][
        "file"
    ].startswith("${ANANTA_VECTOR_INDEX_TASK_SIGNING_KEYRING_SECRET_FILE")
    assert payload["secrets"]["vector-index-task-verification-keyring"][
        "file"
    ].startswith(
        "${ANANTA_VECTOR_INDEX_TASK_VERIFICATION_KEYRING_SECRET_FILE"
    )

    for worker_name in ("ai-agent-alpha", "ai-agent-beta"):
        worker = payload["services"][worker_name]
        assert worker["build"]["args"]["INSTALL_QDRANT_RUNTIME"] == "1"
        assert set(worker["networks"]) == {"default", "qdrant-worker"}
        assert worker["environment"]["ANANTA_QDRANT_API_KEY_REF"].startswith("secretfile://")
        assert worker["environment"]["ANANTA_QDRANT_TLS_CA_CERT_REF"].endswith("qdrant-tls-ca.pem")
        assert {item["source"] for item in worker["secrets"]} == {
            "qdrant-api-key",
            "qdrant-tls-ca",
            "vector-index-task-verification-keyring",
        }
        assert worker["environment"]["ANANTA_VECTOR_INDEX_INPUT_ROOTS"] == "/var/lib/ananta/vector-index-inputs"
        assert worker["environment"][
            "ANANTA_VECTOR_INDEX_TASK_REPLAY_RECEIPT_RETENTION_SECONDS"
        ].endswith(":-86400}")
        assert "qdrant-vector-index-inputs:/var/lib/ananta/vector-index-inputs:ro" in worker["volumes"]
        healthcheck = worker["healthcheck"]
        assert healthcheck["test"][:3] == [
            "CMD",
            "python",
            "-c",
        ]
        readiness_probe = healthcheck["test"][3]
        assert (
            "/internal/worker/vector-index-readiness"
            in readiness_probe
        )
        assert (
            "vector_index_worker_registration"
            in readiness_probe
        )
        assert "hub_registration" in readiness_probe
        for capability in (
            "retrieval",
            "index_write",
            "vector_index_operation",
        ):
            assert capability in readiness_probe

    hub = payload["services"]["ai-agent-hub"]
    assert hub["environment"]["ANANTA_VECTOR_INDEX_INPUT_PUBLISH_ROOT"] == "/var/lib/ananta/vector-index-inputs"
    assert hub["environment"][
        "ANANTA_VECTOR_INDEX_TASK_SIGNING_KEYRING_FILE"
    ].endswith("vector-index-task-signing-keyring.json")
    assert {item["source"] for item in hub["secrets"]} == {
        "vector-index-task-signing-keyring"
    }
    assert "qdrant-vector-index-inputs:/var/lib/ananta/vector-index-inputs:rw" in hub["volumes"]
    assert "qdrant-vector-index-inputs" in payload["volumes"]

    dockerfile = (ROOT / "docker/compose-next/Dockerfile.quickstart-no-ollama").read_text(encoding="utf-8")
    assert "ARG INSTALL_QDRANT_RUNTIME=0" in dockerfile
    assert "requirements.qdrant-worker.lock" in dockerfile


def _qdrant_full_stack_config(
    *,
    env_file: Path,
    clean_home: Path,
) -> subprocess.CompletedProcess[str]:
    if shutil.which("docker") is None:
        pytest.skip("docker CLI is not installed")
    clean_home.mkdir(mode=0o700, exist_ok=True)
    environment = {
        "HOME": str(clean_home),
        "PATH": os.environ.get("PATH", ""),
        "COMPOSE_PROJECT_NAME": "ananta-qdrant-contract",
    }
    return subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            str(env_file),
            "-f",
            str(QUICKSTART_COMPOSE),
            "-f",
            str(DEV_AUTH_OVERLAY),
            "-f",
            str(COMPOSE),
            "-f",
            str(WORKER_OVERLAY),
            "--profile",
            "qdrant",
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


def test_qdrant_full_stack_renders_from_clean_shell_via_env_file(
    tmp_path: Path,
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "INITIAL_ADMIN_PASSWORD=compose-contract-password\n",
        encoding="utf-8",
    )

    completed = _qdrant_full_stack_config(
        env_file=env_file,
        clean_home=tmp_path / "home",
    )

    assert completed.returncode == 0, completed.stderr


def test_qdrant_full_stack_fails_before_start_without_admin_password(
    tmp_path: Path,
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "INITIAL_ADMIN_PASSWORD=\n",
        encoding="utf-8",
    )

    completed = _qdrant_full_stack_config(
        env_file=env_file,
        clean_home=tmp_path / "home",
    )

    assert completed.returncode != 0
    assert "INITIAL_ADMIN_PASSWORD" in completed.stderr


def test_qdrant_quickstart_identity_overlay_is_strict_and_worker_private() -> None:
    payload = yaml.safe_load(
        DEV_AUTH_OVERLAY.read_text(encoding="utf-8")
    )
    services = payload["services"]
    bootstrap = services["workflow-keyring-bootstrap"]

    assert bootstrap["network_mode"] == "none"
    assert bootstrap["read_only"] is True
    assert bootstrap["cap_drop"] == ["ALL"]
    assert bootstrap["security_opt"] == [
        "no-new-privileges:true"
    ]
    assert bootstrap["entrypoint"] == [
        "python",
        "/app/scripts/bootstrap-dev-workflow-keyrings.py",
    ]
    assert bootstrap["volumes"][-1]["bind"][
        "create_host_path"
    ] is False

    hub = services["ai-agent-hub"]
    assert hub["environment"]["SECRET_KEY"] == ""
    assert hub["environment"][
        "ANANTA_SOURCE_ACCESS_ALLOW_COMPOSE_SECRET_DERIVATION"
    ] == "0"
    assert hub["environment"]["ANANTA_SOURCE_ACCESS_KEYRING_FILE"] == (
        "/run/ananta-source-access/source-access-hmac-keyring.json"
    )
    assert (
        hub["environment"][
            "ANANTA_WORKFLOW_REQUIRE_REGISTERED_WORKER_AUTH"
        ]
        == "1"
    )
    assert hub["environment"][
        "ANANTA_WORKFLOW_WORKER_REGISTRATION_KEYRING_FILE"
    ].endswith("/worker-registration-keyring.json")
    assert hub["environment"]["AGENT_TOKEN_FILE"].endswith(
        "/hub-service-token"
    )
    assert hub["environment"]["SECRET_KEY_FILE"].endswith(
        "/hub-session-signing-key"
    )
    assert hub["environment"]["AGENT_TOKEN_PERSISTENCE"] == "0"
    assert hub["depends_on"]["workflow-keyring-bootstrap"][
        "condition"
    ] == "service_completed_successfully"
    hub_mount = next(
        mount
        for mount in hub["volumes"]
        if mount["target"] == "/run/ananta-dev-workflow"
    )
    assert hub_mount["source"].endswith("/hub")
    assert hub_mount["read_only"] is True
    assert hub_mount["bind"]["create_host_path"] is False
    source_access_mount = next(
        mount
        for mount in hub["volumes"]
        if mount["target"] == "/run/ananta-source-access"
    )
    assert source_access_mount["source"].endswith("/worker")
    assert source_access_mount["read_only"] is True
    assert source_access_mount["bind"]["create_host_path"] is False

    private_sources: set[str] = set()
    for worker_name, private_dir in (
        ("ai-agent-alpha", "alpha"),
        ("ai-agent-beta", "beta"),
    ):
        worker = services[worker_name]
        environment = worker["environment"]
        assert environment["SECRET_KEY"] == ""
        assert environment[
            "ANANTA_SOURCE_ACCESS_ALLOW_COMPOSE_SECRET_DERIVATION"
        ] == "0"
        assert environment["ANANTA_SOURCE_ACCESS_KEYRING_FILE"] == (
            "/run/ananta-dev-workflow/public/"
            "source-access-hmac-keyring.json"
        )
        assert environment["DISABLE_INITIAL_ADMIN"] == "1"
        assert environment["INITIAL_ADMIN_USER"] == ""
        assert environment["INITIAL_ADMIN_PASSWORD"] == ""
        assert environment["AGENT_TOKEN_PERSISTENCE"] == "0"
        assert environment["AGENT_TOKEN_FILE"] == (
            "/run/ananta-dev-workflow/private/"
            "worker-service-token"
        )
        assert environment["REGISTRATION_TOKEN_FILE"] == (
            "/run/ananta-dev-workflow/private/"
            "worker-registration-token"
        )
        assert environment["SECRET_KEY_FILE"] == (
            "/run/ananta-dev-workflow/private/"
            "worker-session-signing-key"
        )
        private_mount = next(
            mount
            for mount in worker["volumes"]
            if mount["target"]
            == "/run/ananta-dev-workflow/private"
        )
        assert private_mount["source"].endswith(
            f"/{private_dir}"
        )
        assert private_mount["read_only"] is True
        assert private_mount["bind"]["create_host_path"] is False
        private_sources.add(private_mount["source"])
    assert len(private_sources) == 2


def test_quickstart_build_context_excludes_config_secrets(tmp_path) -> None:
    if shutil.which("docker") is None:
        pytest.skip("docker CLI is not installed")
    context = tmp_path / "context"
    secret_dir = context / "config" / "secrets"
    secret_dir.mkdir(parents=True)
    (context / ".dockerignore").write_text(
        DOCKERIGNORE.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (secret_dir / "private-keyring.json").write_text(
        "synthetic-canary-private-material",
        encoding="utf-8",
    )
    dockerfile_path = context / "Dockerfile"
    dockerfile_path.write_text(
        "\n".join(
            (
                "FROM scratch",
                "COPY config/secrets/private-keyring.json /leaked.json",
            )
        )
        + "\n",
        encoding="utf-8",
    )

    completed = subprocess.run(
        [
            "docker",
            "build",
            "--no-cache",
            "-f",
            str(dockerfile_path),
            str(context),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert completed.returncode != 0
    assert "/config/secrets/" in DOCKERIGNORE.read_text(encoding="utf-8")
    assert "COPY . ." in QUICKSTART_DOCKERFILE.read_text(encoding="utf-8")


def test_qdrant_hub_owns_exact_internal_endpoint_policy_without_network_access() -> None:
    payload = yaml.safe_load(WORKER_OVERLAY.read_text(encoding="utf-8"))
    hub = payload["services"]["ai-agent-hub"]
    environment = hub["environment"]

    assert environment["ANANTA_QDRANT_REST_URL"] == "https://qdrant:6333"
    assert environment["ANANTA_QDRANT_ALLOWED_ORIGINS"] == "https://qdrant:6333"
    assert environment["ANANTA_QDRANT_TRUSTED_PRIVATE_ORIGINS"] == "https://qdrant:6333"
    assert environment["ANANTA_QDRANT_TLS_CA_CERT_REF"].endswith("qdrant-tls-ca.pem")
    assert environment["ANANTA_QDRANT_EXTERNAL_CALLS_ALLOWED"] == "false"
    assert "qdrant-worker" not in hub.get("networks", [])
    assert {item["source"] for item in hub["secrets"]} == {
        "vector-index-task-signing-keyring"
    }


def test_qdrant_hub_read_capability_is_isolated_in_explicit_overlay() -> None:
    payload = yaml.safe_load(CODECOMPASS_HUB_READ_OVERLAY.read_text(encoding="utf-8"))
    hub = payload["services"]["ai-agent-hub"]
    environment = hub["environment"]

    assert hub["build"]["args"]["INSTALL_QDRANT_RUNTIME"] == "1"
    assert set(hub["networks"]) == {"default", "qdrant-worker"}
    assert hub["secrets"][0]["source"] == "qdrant-api-key"
    assert {item["source"] for item in hub["secrets"]} == {
        "qdrant-api-key",
        "qdrant-tls-ca",
    }
    assert environment["HUB_CAN_BE_WORKER"] == "1"
    assert environment["CODECOMPASS_VECTOR_ENABLED"] == "1"
    assert environment["ANANTA_CODECOMPASS_VECTOR_HUB_QDRANT_READ_ENABLED"] == "true"
    assert ":?" in environment["ANANTA_CODECOMPASS_VECTOR_WORKSPACE_ID"]
    assert ":?" in environment["ANANTA_CODECOMPASS_VECTOR_REPOSITORY_ID"]
    assert environment["ANANTA_QDRANT_REST_URL"] == "https://qdrant:6333"
    assert environment["ANANTA_QDRANT_TLS_CA_CERT_REF"].endswith("qdrant-tls-ca.pem")


def test_qdrant_wiki_hub_read_capability_is_a_separate_overlay() -> None:
    payload = yaml.safe_load(WIKI_HUB_READ_OVERLAY.read_text(encoding="utf-8"))
    hub = payload["services"]["ai-agent-hub"]
    environment = hub["environment"]

    assert hub["build"]["args"]["INSTALL_QDRANT_RUNTIME"] == "1"
    assert set(hub["networks"]) == {"default", "qdrant-worker"}
    assert {item["source"] for item in hub["secrets"]} == {
        "qdrant-api-key",
        "qdrant-tls-ca",
    }
    assert environment["HUB_CAN_BE_WORKER"] == "1"
    assert environment["ANANTA_WIKI_VECTOR_HUB_QDRANT_READ_ENABLED"] == "true"
    assert ":?" in environment["ANANTA_WIKI_VECTOR_WORKSPACE_ID"]
    assert ":?" in environment["ANANTA_WIKI_VECTOR_SOURCE_ID"]
    assert environment["ANANTA_WIKI_VECTOR_PROFILE_NAME"] == "${ANANTA_WIKI_VECTOR_PROFILE_NAME:-default}"
    assert environment["ANANTA_QDRANT_REST_URL"] == "https://qdrant:6333"
    assert environment["ANANTA_QDRANT_EXTERNAL_CALLS_ALLOWED"] == "false"
    assert environment["ANANTA_QDRANT_API_KEY_REF"].startswith("secretfile://")
    assert environment["ANANTA_QDRANT_TLS_CA_CERT_REF"].endswith("qdrant-tls-ca.pem")
    assert "CODECOMPASS_VECTOR_ENABLED" not in environment
    assert not any(name.startswith("ANANTA_CODECOMPASS_VECTOR_") for name in environment)


def test_rendered_wiki_hub_environment_matches_runtime_contract() -> None:
    payload = _render_wiki_qdrant_quickstart()
    hub = payload["services"]["ai-agent-hub"]
    environment = hub["environment"]

    assert environment["ANANTA_WIKI_VECTOR_WORKSPACE_ID"] == "wiki-workspace"
    assert environment["ANANTA_WIKI_VECTOR_SOURCE_ID"] == "wiki-source"
    assert environment["ANANTA_WIKI_VECTOR_PROFILE_NAME"] == "wiki-profile"
    assert environment["ANANTA_WIKI_VECTOR_RETRIEVAL_CACHE_STATE"] == "cache-v2"
    assert environment["ANANTA_WIKI_VECTOR_MANIFEST_HASH"] == "manifest-v2"
    assert environment["ANANTA_WIKI_VECTOR_HUB_QDRANT_READ_ENABLED"] == "true"
    assert environment["ANANTA_QDRANT_REST_URL"] == "https://qdrant:6333"
    assert environment["ANANTA_QDRANT_API_KEY_REF"].startswith("secretfile://")
    assert environment["ANANTA_QDRANT_TLS_CA_CERT_REF"].startswith("secretfile://")
    assert (
        environment[
            "ANANTA_WORKFLOW_REQUIRE_REGISTERED_WORKER_AUTH"
        ]
        == "1"
    )
    assert environment[
        "ANANTA_WORKFLOW_WORKER_REGISTRATION_KEYRING_FILE"
    ].endswith("/worker-registration-keyring.json")
    assert "qdrant-worker" in hub["networks"]
    assert {secret["source"] for secret in hub["secrets"]} == {
        "qdrant-api-key",
        "qdrant-tls-ca",
        "vector-index-task-signing-keyring",
    }
    assert "CODECOMPASS_VECTOR_ENABLED" not in environment
    assert not any(name.startswith("ANANTA_CODECOMPASS_VECTOR_") for name in environment)
    for worker_name in ("ai-agent-alpha", "ai-agent-beta"):
        worker = payload["services"][worker_name]
        worker_environment = worker[
            "environment"
        ]
        assert worker_environment["AGENT_TOKEN_FILE"].endswith(
            "/worker-service-token"
        )
        assert worker_environment[
            "REGISTRATION_TOKEN_FILE"
        ].endswith("/worker-registration-token")
        assert worker_environment[
            "ANANTA_WORKFLOW_HUB_TOKEN_FILE"
        ] == worker_environment["AGENT_TOKEN_FILE"]
        assert (
            "/internal/worker/vector-index-readiness"
            in worker["healthcheck"]["test"][3]
        )

    rollout = VectorStoreRolloutService(
        store=InMemoryVectorStoreRolloutStore(),
        global_config={},
        audit=lambda _event, _payload: None,
    )
    rollout.set_workspace_override(
        domain="wiki",
        workspace_id="wiki-workspace",
        override={"provider": "qdrant"},
        expected_revision=0,
        actor="compose-contract",
    )
    wiki_resolver = build_default_wiki_vector_runtime_resolver(
        environ=environment,
        rollout_service=rollout,
        index_task_service=_TaskSubmissionPort(),
        index_input_publisher=_InputPublisherPort(),
    )

    assert wiki_resolver is not None
    runtime = wiki_resolver.resolve()
    assert runtime.allow_hub_qdrant_reads is True
    assert runtime.vector_store_config.provider == "qdrant"
    assert runtime.vector_store_config.vector_scope().domain == "wiki"
    assert build_default_codecompass_vector_runtime_resolver(environ=environment) is None


def test_standalone_profile_contains_no_incomplete_stack_service_fragments() -> None:
    payload = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))

    assert set(payload["services"]) == {"qdrant"}
    assert {"ai-agent-hub", "ai-agent-alpha", "ai-agent-beta"}.issubset(
        yaml.safe_load(WORKER_OVERLAY.read_text(encoding="utf-8"))["services"]
    )
    assert "http://qdrant:6333" not in WORKER_OVERLAY.read_text(encoding="utf-8")
    assert "http://qdrant:6333" not in CODECOMPASS_HUB_READ_OVERLAY.read_text(encoding="utf-8")
    assert "http://qdrant:6333" not in WIKI_HUB_READ_OVERLAY.read_text(encoding="utf-8")


def test_qdrant_ci_renders_grpc_example_and_logs_completed_cleanup_last() -> None:
    workflow = yaml.safe_load(QUALITY_WORKFLOW.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["qdrant-integration"]["steps"]
    render = next(step["run"] for step in steps if step.get("name") == "Render the standalone digest-pinned profile")
    cleanup = next(step["run"] for step in steps if step.get("name") == "Remove run-scoped Qdrant state")

    assert "compose.qdrant-grpc-host.yml" in render
    assert "compose.workflow-runtime.dev-auth.yml" in render
    assert "compose.qdrant-hub-read.yml" in render
    assert "compose.qdrant-wiki-hub-read.yml" in render
    assert render.count("--profile qdrant config --quiet") >= 5
    assert (
        '--env-file "${QDRANT_COMPOSE_ENV_FILE}"'
        in render
    )
    assert (
        render.count(
            "env -u INITIAL_ADMIN_PASSWORD docker compose"
        )
        == 4
    )
    assert "ananta-qdrant-missing-admin.env" in render
    assert "accepted an empty INITIAL_ADMIN_PASSWORD" in render
    assert (
        'grep -q INITIAL_ADMIN_PASSWORD '
        '"${QDRANT_COMPOSE_MISSING_ADMIN_LOG}"'
        in render
    )
    assert cleanup.index('rm -f "${ANANTA_QDRANT_API_KEY_FILE}"') < (cleanup.index("cleanup=completed"))
    assert "cat ci-artifacts/qdrant-integration/versions.txt" in cleanup
    assert "cat ci-artifacts/qdrant-integration/gate.txt" in cleanup
    for field in ("executed=", "skipped=", "cleanup=completed"):
        assert field in cleanup
