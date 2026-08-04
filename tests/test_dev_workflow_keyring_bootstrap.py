from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import yaml
from cryptography.fernet import Fernet

from agent.services.source_access_manifest_signing import (
    HubSourceAccessManifestSigner,
    SourceAccessSigningKey,
    WorkerSourceAccessManifestVerifier,
)
from ananta_contracts.runtime_authorization_crypto import (
    Ed25519SigningKeyRing,
    Ed25519VerificationKeyRing,
)

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/bootstrap-dev-workflow-keyrings.py"
COMPOSE = ROOT / "docker/compose-next/compose.dev.ollama.yml"
DEV_AUTH_COMPOSE = (
    ROOT / "docker/compose-next/compose.workflow-runtime.dev-auth.yml"
)
COMPOSE_BASE = ROOT / "docker/compose-next/compose.base.yml"
DOCKERIGNORE = ROOT / ".dockerignore"


def _run(
    root: Path,
    *extra_args: str,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--root",
            str(root),
            *extra_args,
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_bootstrap_creates_disjoint_valid_keyrings_and_reuses_them(tmp_path):
    root = (tmp_path / "workflow-secrets").resolve()

    created = _run(root)

    assert created.returncode == 0, created.stderr
    assert created.stdout.strip() == "development workflow keyrings created"
    hub_files = {path.name for path in (root / "hub").iterdir()}
    worker_files = {path.name for path in (root / "worker").iterdir()}
    assert hub_files == {
        "workflow-auth-signing-keyring.json",
        "workflow-dispatch-keyring.json",
        "worker-registration-keyring.json",
        "hub-service-token",
        "hub-session-signing-key",
    }
    assert worker_files == {
        "workflow-auth-verification-keyring.json",
        "source-access-hmac-keyring.json",
    }
    assert {path.name for path in (root / "alpha").iterdir()} == {
        "worker-service-token",
        "worker-registration-token",
        "worker-session-signing-key",
    }
    assert {path.name for path in (root / "beta").iterdir()} == {
        "worker-service-token",
        "worker-registration-token",
        "worker-session-signing-key",
    }

    signing_path = root / "hub/workflow-auth-signing-keyring.json"
    verification_path = root / "worker/workflow-auth-verification-keyring.json"
    dispatch_path = root / "hub/workflow-dispatch-keyring.json"
    signing = json.loads(signing_path.read_text(encoding="utf-8"))
    verification = json.loads(verification_path.read_text(encoding="utf-8"))
    dispatch = json.loads(dispatch_path.read_text(encoding="utf-8"))
    source_access_path = root / "worker/source-access-hmac-keyring.json"
    source_access = json.loads(
        source_access_path.read_text(encoding="utf-8")
    )
    signer = Ed25519SigningKeyRing.from_mapping(signing)
    verifier = Ed25519VerificationKeyRing.from_mapping(verification)
    key_id, signature = signer.sign(namespace="test", payload={"value": 1})
    verifier.verify(
        namespace="test",
        payload={"value": 1},
        key_id=key_id,
        signature=signature,
        contract_id="bootstrap-test",
    )
    dispatch_key_id = dispatch["active_key_id"]
    Fernet(dispatch["keys"][dispatch_key_id].encode("ascii"))
    assert source_access["schema"] == (
        "ananta.source-access-hmac-keyring.v1"
    )
    source_access_key_id = source_access["active_key_id"]
    source_access_secret = base64.b64decode(
        source_access["keys"][source_access_key_id],
        validate=True,
    )
    assert len(source_access_secret) >= 32
    source_access_digest = "a" * 64
    source_access_signature = HubSourceAccessManifestSigner(
        SourceAccessSigningKey(
            key_id=source_access_key_id,
            secret=source_access_secret,
        )
    ).sign(manifest_digest=source_access_digest)
    assert WorkerSourceAccessManifestVerifier(
        {source_access_key_id: source_access_secret}
    ).verify(
        manifest_digest=source_access_digest,
        signature=source_access_signature,
    )
    assert stat.S_IMODE(signing_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(dispatch_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(verification_path.stat().st_mode) == 0o444
    assert stat.S_IMODE(source_access_path.stat().st_mode) == 0o600

    private_paths = [
        root / "hub/hub-service-token",
        root / "hub/hub-session-signing-key",
        root / "alpha/worker-service-token",
        root / "alpha/worker-registration-token",
        root / "alpha/worker-session-signing-key",
        root / "beta/worker-service-token",
        root / "beta/worker-registration-token",
        root / "beta/worker-session-signing-key",
    ]
    private_values = [
        path.read_text(encoding="utf-8").strip()
        for path in private_paths
    ]
    assert len(set(private_values)) == len(private_values)
    assert all(len(value.encode("utf-8")) >= 32 for value in private_values)
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in private_paths)

    registration_path = root / "hub/worker-registration-keyring.json"
    registration = json.loads(registration_path.read_text(encoding="utf-8"))
    assert registration["schema"] == "ananta.workflow-worker-registration-keyring.v1"
    assert set(registration["workers"]) == {
        "ananta-worker-1",
        "ananta-worker-2",
    }
    for worker_id, logical_name, worker_url in (
        ("ananta-worker-1", "alpha", "http://ai-agent-alpha:5000"),
        ("ananta-worker-2", "beta", "http://ai-agent-beta:5000"),
    ):
        row = registration["workers"][worker_id]
        service_token = (
            root / logical_name / "worker-service-token"
        ).read_text(encoding="utf-8").strip()
        registration_token = (
            root / logical_name / "worker-registration-token"
        ).read_text(encoding="utf-8").strip()
        session_key = (
            root / logical_name / "worker-session-signing-key"
        ).read_text(encoding="utf-8").strip()
        assert row["worker_url"] == worker_url
        assert row["registration_token"] == registration_token
        assert row["service_token_sha256"] == hashlib.sha256(
            service_token.encode("utf-8")
        ).hexdigest()
        assert row["session_signing_key_sha256"] == hashlib.sha256(
            session_key.encode("utf-8")
        ).hexdigest()
        assert "planning" in row["allowed_capabilities"]
        assert {
            "retrieval",
            "index_write",
            "source_analysis",
            "vector_index_operation",
        }.issubset(row["allowed_capabilities"])
    assert stat.S_IMODE(registration_path.stat().st_mode) == 0o600

    before = {
        path: _digest(path)
        for path in (
            signing_path,
            verification_path,
            dispatch_path,
            source_access_path,
            registration_path,
            *private_paths,
        )
    }
    reused = _run(root)
    assert reused.returncode == 0, reused.stderr
    assert reused.stdout.strip() == "development workflow keyrings reused"
    assert {path: _digest(path) for path in before} == before


def test_bootstrap_fails_closed_for_partial_keyring_set(tmp_path):
    root = (tmp_path / "workflow-secrets").resolve()
    hub = root / "hub"
    hub.mkdir(parents=True)
    existing = hub / "workflow-auth-signing-keyring.json"
    existing.write_text("{}\n", encoding="utf-8")
    existing.chmod(0o600)
    before = existing.read_bytes()

    result = _run(root)

    assert result.returncode == 64
    assert "incomplete development workflow keyring set" in result.stderr
    assert existing.read_bytes() == before
    assert not (root / "worker/workflow-auth-verification-keyring.json").exists()
    assert not (hub / "workflow-dispatch-keyring.json").exists()


def test_bootstrap_upgrades_legacy_authorization_keyrings_without_rotation(
    tmp_path,
):
    root = (tmp_path / "workflow-secrets").resolve()
    assert _run(root).returncode == 0
    authorization_paths = (
        root / "hub/workflow-auth-signing-keyring.json",
        root / "worker/workflow-auth-verification-keyring.json",
        root / "hub/workflow-dispatch-keyring.json",
    )
    before = {
        path: _digest(path) for path in authorization_paths
    }
    for path in (
        root / "hub/worker-registration-keyring.json",
        root / "hub/hub-service-token",
        root / "hub/hub-session-signing-key",
        root / "alpha/worker-service-token",
        root / "alpha/worker-registration-token",
        root / "alpha/worker-session-signing-key",
        root / "beta/worker-service-token",
        root / "beta/worker-registration-token",
        root / "beta/worker-session-signing-key",
    ):
        path.unlink()

    upgraded = _run(root)

    assert upgraded.returncode == 0, upgraded.stderr
    assert upgraded.stdout.strip() == (
        "development workflow keyrings upgraded"
    )
    assert {
        path: _digest(path) for path in authorization_paths
    } == before
    assert (
        root / "hub/worker-registration-keyring.json"
    ).is_file()


def test_bootstrap_adds_source_access_keyring_without_rotating_credentials(
    tmp_path,
):
    root = (tmp_path / "workflow-secrets").resolve()
    assert _run(root).returncode == 0
    source_access_path = root / "worker/source-access-hmac-keyring.json"
    source_access_path.unlink()
    stable_paths = tuple(path for path in root.rglob("*") if path.is_file())
    before = {path: _digest(path) for path in stable_paths}

    upgraded = _run(root)

    assert upgraded.returncode == 0, upgraded.stderr
    assert upgraded.stdout.strip() == "development workflow keyrings upgraded"
    assert {path: _digest(path) for path in stable_paths} == before
    assert source_access_path.is_file()


def test_bootstrap_recovers_legacy_create_before_source_access_upgrade(
    tmp_path,
):
    root = (tmp_path / "workflow-secrets").resolve()
    assert _run(root).returncode == 0
    (root / "worker/source-access-hmac-keyring.json").unlink()
    spec = importlib.util.spec_from_file_location(
        "bootstrap_dev_workflow_keyrings_recovery_test",
        SCRIPT,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    paths = module._paths(root)
    transaction_path = root / ".bootstrap-transaction.json"
    transaction_path.write_text(
        json.dumps(
            {
                "schema": "ananta.dev-workflow-bootstrap-transaction.v1",
                "mode": "create",
                "staging_name": ".bootstrap-staging-legacy-create",
                "target_hashes": {
                    name: _digest(paths[name])
                    for name in module._LEGACY_ALL_DOCUMENTS
                },
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    transaction_path.chmod(0o600)

    recovered = _run(root)

    assert recovered.returncode == 0, recovered.stderr
    assert recovered.stdout.strip() == "development workflow keyrings upgraded"
    assert not transaction_path.exists()
    assert (root / "worker/source-access-hmac-keyring.json").is_file()


def test_bootstrap_upgrades_known_capabilities_before_source_access(
    tmp_path,
):
    root = (tmp_path / "workflow-secrets").resolve()
    assert _run(root).returncode == 0
    source_access_path = root / "worker/source-access-hmac-keyring.json"
    source_access_path.unlink()
    registration_path = root / "hub/worker-registration-keyring.json"
    registration = json.loads(
        registration_path.read_text(encoding="utf-8")
    )
    for row in registration["workers"].values():
        row["allowed_capabilities"].remove("source_analysis")
    registration_path.write_text(
        json.dumps(
            registration,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    registration_path.chmod(0o600)
    stable_paths = tuple(
        path
        for path in root.rglob("*")
        if path.is_file() and path != registration_path
    )
    before = {path: _digest(path) for path in stable_paths}

    upgraded = _run(root)

    assert upgraded.returncode == 0, upgraded.stderr
    assert upgraded.stdout.strip() == "development workflow keyrings upgraded"
    assert {path: _digest(path) for path in stable_paths} == before
    assert source_access_path.is_file()
    upgraded_registration = json.loads(
        registration_path.read_text(encoding="utf-8")
    )
    assert all(
        "source_analysis" in row["allowed_capabilities"]
        for row in upgraded_registration["workers"].values()
    )


def test_bootstrap_adds_vector_capabilities_without_rotating_credentials(
    tmp_path,
):
    root = (tmp_path / "workflow-secrets").resolve()
    assert _run(root).returncode == 0
    registration_path = (
        root / "hub/worker-registration-keyring.json"
    )
    registration = json.loads(
        registration_path.read_text(encoding="utf-8")
    )
    for row in registration["workers"].values():
        row["allowed_capabilities"] = [
            capability
            for capability in row["allowed_capabilities"]
            if capability not in {
                "index_write",
                "vector_index_operation",
            }
        ]
    registration_path.write_text(
        json.dumps(
            registration,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    registration_path.chmod(0o600)
    private_paths = tuple(
        path
        for path in root.rglob("*")
        if path.is_file() and path != registration_path
    )
    before = {
        path: _digest(path)
        for path in private_paths
    }

    upgraded = _run(root)

    assert upgraded.returncode == 0, upgraded.stderr
    assert upgraded.stdout.strip() == (
        "development workflow keyrings upgraded"
    )
    assert {
        path: _digest(path)
        for path in private_paths
    } == before
    upgraded_registration = json.loads(
        registration_path.read_text(encoding="utf-8")
    )
    for row in upgraded_registration["workers"].values():
        assert {
            "retrieval",
            "index_write",
            "vector_index_operation",
        }.issubset(row["allowed_capabilities"])
    reused = _run(root)
    assert reused.returncode == 0, reused.stderr
    assert reused.stdout.strip() == (
        "development workflow keyrings reused"
    )


def test_bootstrap_adds_source_analysis_without_rotating_credentials(
    tmp_path,
):
    root = (tmp_path / "workflow-secrets").resolve()
    assert _run(root).returncode == 0
    registration_path = root / "hub/worker-registration-keyring.json"
    registration = json.loads(
        registration_path.read_text(encoding="utf-8")
    )
    for row in registration["workers"].values():
        row["allowed_capabilities"].remove("source_analysis")
    registration_path.write_text(
        json.dumps(
            registration,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    registration_path.chmod(0o600)
    private_paths = tuple(
        path
        for path in root.rglob("*")
        if path.is_file() and path != registration_path
    )
    before = {path: _digest(path) for path in private_paths}

    upgraded = _run(root)

    assert upgraded.returncode == 0, upgraded.stderr
    assert upgraded.stdout.strip() == "development workflow keyrings upgraded"
    assert {path: _digest(path) for path in private_paths} == before
    upgraded_registration = json.loads(
        registration_path.read_text(encoding="utf-8")
    )
    for row in upgraded_registration["workers"].values():
        assert "source_analysis" in row["allowed_capabilities"]

    reused = _run(root)
    assert reused.returncode == 0, reused.stderr
    assert reused.stdout.strip() == "development workflow keyrings reused"


def test_bootstrap_does_not_upgrade_an_unknown_capability_edit(
    tmp_path,
):
    root = (tmp_path / "workflow-secrets").resolve()
    assert _run(root).returncode == 0
    registration_path = (
        root / "hub/worker-registration-keyring.json"
    )
    registration = json.loads(
        registration_path.read_text(encoding="utf-8")
    )
    registration["workers"]["ananta-worker-1"][
        "allowed_capabilities"
    ].remove("planning")
    registration_path.write_text(
        json.dumps(
            registration,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    registration_path.chmod(0o600)
    before = registration_path.read_bytes()

    rejected = _run(root)

    assert rejected.returncode == 64
    assert (
        "registration keyring does not match credentials"
        in rejected.stderr
    )
    assert registration_path.read_bytes() == before


def test_bootstrap_rejects_a_symlinked_secret_root(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    linked_root = tmp_path / "linked"
    linked_root.symlink_to(target, target_is_directory=True)

    result = _run(linked_root.absolute())

    assert result.returncode == 64
    assert "keyring root must not be a symlink" in result.stderr
    assert list(target.iterdir()) == []


def test_bootstrap_rejects_unexpected_worker_directory_entries(tmp_path):
    root = (tmp_path / "workflow-secrets").resolve()
    worker = root / "worker"
    worker.mkdir(parents=True)
    unexpected = worker / "private-material.json"
    unexpected.write_text("{}\n", encoding="utf-8")
    unexpected.chmod(0o600)

    result = _run(root)

    assert result.returncode == 64
    assert "unexpected entry in development workflow worker_dir" in result.stderr


def test_bootstrap_can_assign_credentials_to_the_wsl_host_user(tmp_path):
    root = (tmp_path / "workflow-secrets").resolve()

    result = _run(
        root,
        "--owner-uid",
        str(os.getuid()),
        "--owner-gid",
        str(os.getgid()),
    )

    assert result.returncode == 0, result.stderr
    for path in (root, *root.rglob("*")):
        assert path.stat().st_uid == os.getuid()
        assert path.stat().st_gid == os.getgid()


def test_ollama_dev_compose_bootstraps_least_privilege_workflow_keyrings():
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    services = compose["services"]
    bootstrap = services["workflow-keyring-bootstrap"]
    assert bootstrap["entrypoint"] == [
        "python",
        "/app/scripts/bootstrap-dev-workflow-keyrings.py",
    ]
    assert bootstrap["command"] == [
        "--root",
        "/run/ananta-dev-workflow",
        "--alpha-worker-id",
        "${ANANTA_WORKER_ALPHA_ID:-ananta-worker-1}",
        "--beta-worker-id",
        "${ANANTA_WORKER_BETA_ID:-ananta-worker-2}",
        "--owner-uid",
        "${ANANTA_HOST_UID:-1000}",
        "--owner-gid",
        "${ANANTA_HOST_GID:-1000}",
    ]

    hub = services["ai-agent-hub"]
    assert hub["environment"]["CORS_ORIGINS"] == (
        "${CORS_ORIGINS:-http://localhost:4200,http://127.0.0.1:4200}"
    )
    assert hub["environment"]["ANANTA_WORKFLOW_AUTH_SIGNING_KEYRING_FILE"].endswith(
        "/workflow-auth-signing-keyring.json"
    )
    assert hub["environment"]["ANANTA_WORKFLOW_DISPATCH_KEYRING_FILE"].endswith(
        "/workflow-dispatch-keyring.json"
    )
    assert (
        hub["environment"]["ANANTA_WORKFLOW_REQUIRE_REGISTERED_WORKER_AUTH"]
        == "1"
    )
    assert hub["environment"][
        "ANANTA_WORKFLOW_WORKER_REGISTRATION_KEYRING_FILE"
    ].endswith("/worker-registration-keyring.json")
    assert hub["environment"]["ANANTA_SOURCE_ACCESS_KEYRING_FILE"] == (
        "/run/ananta-source-access/source-access-hmac-keyring.json"
    )
    assert hub["environment"][
        "ANANTA_SOURCE_ACCESS_ALLOW_COMPOSE_SECRET_DERIVATION"
    ] == "0"
    assert hub["environment"]["AGENT_TOKEN_FILE"].endswith(
        "/hub-service-token"
    )
    assert hub["environment"]["SECRET_KEY_FILE"].endswith(
        "/hub-session-signing-key"
    )
    assert hub["environment"]["AGENT_TOKEN_PERSISTENCE"] == "0"
    assert (
        hub["depends_on"]["workflow-keyring-bootstrap"]["condition"]
        == "service_completed_successfully"
    )
    hub_mount = next(
        mount
        for mount in hub["volumes"]
        if isinstance(mount, dict)
        and mount["target"] == "/run/ananta-dev-workflow"
    )
    assert hub_mount["source"].endswith("/hub")
    assert hub_mount["read_only"] is True
    source_access_mount = next(
        mount
        for mount in hub["volumes"]
        if isinstance(mount, dict)
        and mount["target"] == "/run/ananta-source-access"
    )
    assert source_access_mount["source"].endswith("/worker")
    assert source_access_mount["read_only"] is True

    for name, private_dir in (
        ("ai-agent-alpha", "alpha"),
        ("ai-agent-beta", "beta"),
    ):
        worker = services[name]
        assert set(worker["environment"]) & {
            "ANANTA_WORKFLOW_AUTH_SIGNING_KEYRING_FILE",
            "ANANTA_WORKFLOW_DISPATCH_KEYRING_FILE",
        } == set()
        assert worker["environment"][
            "ANANTA_WORKFLOW_AUTH_VERIFICATION_KEYRING_FILE"
        ] == (
            "/run/ananta-dev-workflow/public/"
            "workflow-auth-verification-keyring.json"
        )
        assert worker["environment"]["ANANTA_SOURCE_ACCESS_KEYRING_FILE"] == (
            "/run/ananta-dev-workflow/public/"
            "source-access-hmac-keyring.json"
        )
        assert worker["environment"][
            "ANANTA_SOURCE_ACCESS_ALLOW_COMPOSE_SECRET_DERIVATION"
        ] == "0"
        assert worker["environment"]["AGENT_TOKEN_FILE"] == (
            "/run/ananta-dev-workflow/private/worker-service-token"
        )
        assert worker["environment"]["SECRET_KEY_FILE"] == (
            "/run/ananta-dev-workflow/private/worker-session-signing-key"
        )
        assert worker["environment"]["REGISTRATION_TOKEN_FILE"] == (
            "/run/ananta-dev-workflow/private/worker-registration-token"
        )
        assert worker["environment"]["ANANTA_WORKFLOW_HUB_TOKEN_FILE"] == (
            "/run/ananta-dev-workflow/private/worker-service-token"
        )
        assert worker["environment"]["ANANTA_WORKFLOW_HUB_URL"] == (
            "http://ai-agent-hub:5000"
        )
        assert worker["environment"]["AGENT_TOKEN_PERSISTENCE"] == "0"
        assert worker["environment"]["DISABLE_INITIAL_ADMIN"] == "1"
        public_mount = next(
            value
            for value in worker["volumes"]
            if isinstance(value, dict)
            and value["target"] == "/run/ananta-dev-workflow/public"
        )
        private_mount = next(
            value
            for value in worker["volumes"]
            if isinstance(value, dict)
            and value["target"] == "/run/ananta-dev-workflow/private"
        )
        assert public_mount["source"].endswith("/worker")
        assert public_mount["read_only"] is True
        assert private_mount["source"].endswith(f"/{private_dir}")
        assert private_mount["read_only"] is True


def test_stack_dev_auth_overlay_prepares_unprivileged_runtime_ownership():
    compose = yaml.safe_load(
        DEV_AUTH_COMPOSE.read_text(encoding="utf-8")
    )
    services = compose["services"]
    runtime = services["runtime-data-bootstrap"]

    assert runtime["user"] == "0:0"
    assert runtime["network_mode"] == "none"
    assert runtime["read_only"] is True
    assert set(runtime["cap_drop"]) == {"ALL"}
    assert set(runtime["cap_add"]) == {"CHOWN", "DAC_READ_SEARCH"}
    assert runtime["entrypoint"] == [
        "python",
        "/app/scripts/bootstrap-dev-runtime-ownership.py",
    ]
    assert set(compose["volumes"]) == {"frontend-angular-cache"}

    for name in ("ai-agent-hub", "ai-agent-alpha", "ai-agent-beta"):
        service = services[name]
        assert service["user"] == (
            "${ANANTA_HOST_UID:-1000}:${ANANTA_HOST_GID:-1000}"
        )
        assert service["environment"]["HOME"] == "/app/data/home"
        assert service["environment"]["XDG_CACHE_HOME"] == (
            "/app/data/cache"
        )
        assert service["depends_on"]["runtime-data-bootstrap"] == {
            "condition": "service_completed_successfully"
        }

    hub = services["ai-agent-hub"]
    assert hub["environment"]["CORS_ORIGINS"] == (
        "${CORS_ORIGINS:-http://localhost:4200,http://127.0.0.1:4200}"
    )
    assert hub["environment"]["ANANTA_SOURCE_ACCESS_KEYRING_FILE"] == (
        "/run/ananta-source-access/source-access-hmac-keyring.json"
    )
    assert hub["environment"][
        "ANANTA_SOURCE_ACCESS_ALLOW_COMPOSE_SECRET_DERIVATION"
    ] == "0"
    source_access_mount = next(
        mount
        for mount in hub["volumes"]
        if mount["target"] == "/run/ananta-source-access"
    )
    assert source_access_mount["source"].endswith("/worker")
    assert source_access_mount["read_only"] is True
    assert source_access_mount["bind"]["create_host_path"] is False
    for worker_name in ("ai-agent-alpha", "ai-agent-beta"):
        assert services[worker_name]["environment"][
            "ANANTA_SOURCE_ACCESS_KEYRING_FILE"
        ] == (
            "/run/ananta-dev-workflow/public/"
            "source-access-hmac-keyring.json"
        )
        assert services[worker_name]["environment"][
            "ANANTA_SOURCE_ACCESS_ALLOW_COMPOSE_SECRET_DERIVATION"
        ] == "0"
    assert services["workflow-keyring-bootstrap"]["depends_on"][
        "runtime-data-bootstrap"
    ] == {"condition": "service_completed_successfully"}


def test_ollama_dev_compose_effectively_clears_inherited_worker_secrets():
    overlay = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    base = yaml.safe_load(COMPOSE_BASE.read_text(encoding="utf-8"))
    services = overlay["services"]

    base_by_service = {
        "ai-agent-hub": "ai-agent-hub-base",
        "ai-agent-alpha": "ai-agent-worker-base",
        "ai-agent-beta": "ai-agent-worker-base",
    }
    for service_name, base_name in base_by_service.items():
        effective_environment = {
            **base["services"][base_name]["environment"],
            **services[service_name]["environment"],
        }
        assert effective_environment["SECRET_KEY"] == ""
        assert effective_environment["SECRET_KEY_FILE"].endswith(
            "session-signing-key"
        )
        volume_sources = [
            (
                str(volume.get("source") or "")
                if isinstance(volume, dict)
                else str(volume).split(":", 1)[0]
            )
            for volume in services[service_name]["volumes"]
        ]
        assert "../.." not in volume_sources

    for worker_name in ("ai-agent-alpha", "ai-agent-beta"):
        effective_environment = {
            **base["services"]["ai-agent-worker-base"]["environment"],
            **services[worker_name]["environment"],
        }
        assert effective_environment["INITIAL_ADMIN_USER"] == ""
        assert effective_environment["INITIAL_ADMIN_PASSWORD"] == ""
        assert effective_environment["OPENROUTER_API_KEY"] == ""
        assert effective_environment["REDIS_URL"] == ""
        assert effective_environment["DATABASE_URL"].startswith(
            "sqlite:////app/data/"
        )


def test_local_workflow_keyrings_are_excluded_from_docker_build_context():
    entries = {
        line.strip()
        for line in DOCKERIGNORE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }

    assert ".ananta/" in entries


def test_bootstrap_module_has_no_import_time_file_writes(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "dev_workflow_keyring_bootstrap",
        SCRIPT,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)

    spec.loader.exec_module(module)

    assert list(tmp_path.iterdir()) == []
