from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import tarfile
from pathlib import Path

import pytest

from scripts.ananta_backup.bind_verification import (
    OllamaModelVerifier,
    WorkflowCredentialVerifier,
)
from scripts.ananta_backup.archive import SafeTarExtractor
from scripts.ananta_backup.errors import BackupError

ROOT = Path(__file__).resolve().parents[2]
BOOTSTRAP = ROOT / "scripts/bootstrap-dev-workflow-keyrings.py"


def _load_bootstrap():
    spec = importlib.util.spec_from_file_location(
        "backup_test_workflow_keyring_bootstrap",
        BOOTSTRAP,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_workflow_credential_verifier_accepts_bootstrap_output(
    tmp_path: Path,
) -> None:
    root = tmp_path / "workflow"
    root.mkdir(mode=0o700)
    bootstrap = _load_bootstrap()
    bootstrap.bootstrap(
        root,
        alpha_worker_id="ananta-worker-1",
        beta_worker_id="ananta-worker-2",
    )

    WorkflowCredentialVerifier().verify(root)


def test_workflow_credential_verifier_rejects_broken_worker_binding(
    tmp_path: Path,
) -> None:
    root = tmp_path / "workflow"
    root.mkdir(mode=0o700)
    bootstrap = _load_bootstrap()
    bootstrap.bootstrap(
        root,
        alpha_worker_id="ananta-worker-1",
        beta_worker_id="ananta-worker-2",
    )
    (root / "alpha" / "worker-service-token").write_text(
        "A" * 48,
        encoding="utf-8",
    )

    with pytest.raises(BackupError, match="alpha credential binding"):
        WorkflowCredentialVerifier().verify(root)


def test_workflow_credential_verifier_rejects_extra_token_newline(
    tmp_path: Path,
) -> None:
    root = tmp_path / "workflow"
    root.mkdir(mode=0o700)
    bootstrap = _load_bootstrap()
    bootstrap.bootstrap(
        root,
        alpha_worker_id="ananta-worker-1",
        beta_worker_id="ananta-worker-2",
    )
    service_token = root / "alpha" / "worker-service-token"
    service_token.write_bytes(service_token.read_bytes() + b"\n")

    with pytest.raises(BackupError, match="token is invalid"):
        WorkflowCredentialVerifier().verify(root)


def test_workflow_credential_verifier_rejects_key_identifier_mismatch(
    tmp_path: Path,
) -> None:
    root = tmp_path / "workflow"
    root.mkdir(mode=0o700)
    bootstrap = _load_bootstrap()
    bootstrap.bootstrap(
        root,
        alpha_worker_id="ananta-worker-1",
        beta_worker_id="ananta-worker-2",
    )
    verification_path = (
        root / "worker" / "workflow-auth-verification-keyring.json"
    )
    verification = json.loads(verification_path.read_text(encoding="utf-8"))
    public_key = next(iter(verification["public_keys"].values()))
    verification["public_keys"] = {"different-key-id": public_key}
    verification_path.chmod(0o600)
    verification_path.write_text(json.dumps(verification), encoding="utf-8")
    verification_path.chmod(0o444)

    with pytest.raises(BackupError, match="key identifiers do not match"):
        WorkflowCredentialVerifier().verify(root)


def test_workflow_credential_verifier_rejects_private_public_mismatch(
    tmp_path: Path,
) -> None:
    root = tmp_path / "workflow"
    root.mkdir(mode=0o700)
    bootstrap = _load_bootstrap()
    bootstrap.bootstrap(
        root,
        alpha_worker_id="ananta-worker-1",
        beta_worker_id="ananta-worker-2",
    )
    signing_path = root / "hub" / "workflow-auth-signing-keyring.json"
    signing = json.loads(signing_path.read_text(encoding="utf-8"))
    active_key_id = signing["active_key_id"]
    signing["private_keys"][active_key_id] = base64.b64encode(
        b"x" * 32
    ).decode("ascii")
    signing_path.write_text(json.dumps(signing), encoding="utf-8")

    with pytest.raises(BackupError, match="private keys do not match"):
        WorkflowCredentialVerifier().verify(root)


def test_safe_archive_extraction_preserves_verifiable_credentials(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir(mode=0o700)
    bootstrap = _load_bootstrap()
    bootstrap.bootstrap(
        source,
        alpha_worker_id="ananta-worker-1",
        beta_worker_id="ananta-worker-2",
    )
    archive_path = tmp_path / "workflow.tar"
    with tarfile.open(archive_path, mode="w") as archive:
        archive.add(source, arcname="workflow")
    destination = tmp_path / "restored"
    destination.mkdir(mode=0o700)

    SafeTarExtractor.extract_file(archive_path, destination)

    verification_mode = (
        destination
        / "workflow"
        / "worker"
        / "workflow-auth-verification-keyring.json"
    ).stat().st_mode & 0o777
    assert verification_mode == 0o400
    WorkflowCredentialVerifier().verify(destination / "workflow")


def _ollama_models(tmp_path: Path) -> tuple[Path, Path]:
    models = tmp_path / "models"
    blobs = models / "blobs"
    manifest = (
        models
        / "manifests"
        / "registry.ollama.ai"
        / "library"
        / "phi4-mini"
        / "latest"
    )
    blobs.mkdir(parents=True)
    manifest.parent.mkdir(parents=True)
    content = b"quantized-test-model"
    digest = hashlib.sha256(content).hexdigest()
    blob = blobs / f"sha256-{digest}"
    blob.write_bytes(content)
    manifest.write_text(
        json.dumps(
            {
                "schemaVersion": 2,
                "config": {
                    "digest": f"sha256:{digest}",
                    "size": len(content),
                },
                "layers": [
                    {
                        "digest": f"sha256:{digest}",
                        "size": len(content),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return models, blob


def test_ollama_model_verifier_hashes_manifest_blobs(tmp_path: Path) -> None:
    models, _ = _ollama_models(tmp_path)

    OllamaModelVerifier().verify(models)


def test_ollama_model_verifier_rejects_tampered_blob(tmp_path: Path) -> None:
    models, blob = _ollama_models(tmp_path)
    blob.write_bytes(b"tampered")

    with pytest.raises(BackupError, match="digest does not match"):
        OllamaModelVerifier().verify(models)


def test_ollama_model_verifier_rejects_wrong_descriptor_size(
    tmp_path: Path,
) -> None:
    models, _ = _ollama_models(tmp_path)
    manifest = next((models / "manifests").rglob("latest"))
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["config"]["size"] += 1
    payload["layers"][0]["size"] += 1
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(BackupError, match="blob size does not match"):
        OllamaModelVerifier().verify(models)
