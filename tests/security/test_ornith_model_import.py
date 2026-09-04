from __future__ import annotations

import hashlib
import json
import os
from dataclasses import replace
from pathlib import Path

import pytest

from agent.services.model_artifact_import_service import (
    ModelArtifactAdmissionError,
    ModelArtifactImportRequest,
    ModelArtifactImportService,
    ModelImportPolicyLoader,
    ModelSourceManifestLoader,
)
from scripts.security.scan_ornith_artifacts import scan
from worker.training.model_imports import (
    ImmutableModelImportExecutor,
    ModelImportCommand,
    ModelImportError,
)

ROOT = Path(__file__).resolve().parents[2]


class NoDownloads:
    def list_files(self, **_kwargs):
        raise AssertionError("network must not be used")

    def download(self, **_kwargs):
        raise AssertionError("network must not be used")


def _tree_hash(name: str, content: bytes) -> str:
    digest = hashlib.sha256()
    digest.update(name.encode())
    digest.update(b"\0")
    digest.update(content)
    digest.update(b"\0")
    return digest.hexdigest()


def test_hub_policy_admits_declared_license_for_evaluation_only() -> None:
    manifest = ModelSourceManifestLoader().load(ROOT / "config/models/ornith-1.5-sources.v1.json")
    policy = ModelImportPolicyLoader().load(ROOT / "config/security/model-import-policy.v1.json")
    service = ModelArtifactImportService(manifest, policy)
    request = ModelArtifactImportRequest(
        tenant_id="tenant-a",
        project_id="ananta",
        task_id="task-a",
        assignment_id="assignment-a",
        dispatch_lease_id="lease-a",
        artifact_id="ornith-1.5-9b-q4-k-m",
        expected_sha256="70c112196e0b7023803c9762752e46d29e612a92c83f995bc3ba1ceb07e8fab6",
        network_authorized=True,
        purpose="evaluation",
    )

    assert service.prepare(request).model_format == "gguf"
    with pytest.raises(ModelArtifactAdmissionError, match="model_artifact_production_not_eligible"):
        service.prepare(replace(request, purpose="production"))


def test_worker_local_import_is_idempotent_and_read_only(tmp_path: Path) -> None:
    content = b"GGUF-safe-fixture"
    artifact = tmp_path / "artifacts" / "ornith-fixture"
    artifact.mkdir(parents=True)
    (artifact / "model.gguf").write_bytes(content)
    executor = ImmutableModelImportExecutor(
        cache_root=tmp_path / "cache", artifact_root=tmp_path / "artifacts", downloads=NoDownloads()
    )
    command = ModelImportCommand(
        tenant_id="tenant-a",
        project_id="ananta",
        source_id="source-a",
        kind="local_artifact",
        expected_sha256=_tree_hash("model.gguf", content),
        artifact_id="ornith-fixture",
        model_id=None,
        revision=None,
        max_bytes=1024,
        allow_patterns=(),
        trust_remote_code=False,
        license_status="approved",
        model_format="gguf",
        architecture="qwen3_5",
        quantization="q4_k_m",
    )

    first = executor.execute(command)
    assert first == executor.execute(command)
    cached = tmp_path / "cache" / first.relative_path / "model.gguf"
    assert cached.stat().st_mode & 0o222 == 0


@pytest.mark.parametrize("extension", ["model.pkl", "model.pickle", "model.py", "model.so"])
def test_policy_forbids_executable_or_pickle_extensions(extension: str) -> None:
    policy = json.loads((ROOT / "config/security/model-import-policy.v1.json").read_text())
    assert any(extension.endswith(item) for item in policy["forbidden_extensions"])


def test_worker_rejects_symlink_and_digest_mismatch(tmp_path: Path) -> None:
    root = tmp_path / "artifacts" / "unsafe"
    root.mkdir(parents=True)
    target = tmp_path / "outside.gguf"
    target.write_bytes(b"GGUF")
    os.symlink(target, root / "model.gguf")
    executor = ImmutableModelImportExecutor(
        cache_root=tmp_path / "cache", artifact_root=tmp_path / "artifacts", downloads=NoDownloads()
    )
    base = dict(
        tenant_id="tenant-a",
        project_id="ananta",
        source_id="source-a",
        kind="local_artifact",
        artifact_id="unsafe",
        model_id=None,
        revision=None,
        max_bytes=1024,
        allow_patterns=(),
        trust_remote_code=False,
        license_status="approved",
        model_format="gguf",
        architecture="qwen3_5",
    )
    with pytest.raises(ModelImportError) as captured:
        executor.execute(ModelImportCommand(expected_sha256="0" * 64, **base))
    assert captured.value.code == "model_artifact_symlink_forbidden"


def test_scanner_checks_raw_digest_size_and_gguf_magic(tmp_path: Path) -> None:
    content = b"GGUFfixture"
    digest = hashlib.sha256(content).hexdigest()
    artifact = tmp_path / "model.gguf"
    artifact.write_bytes(content)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema": "ananta.model-source-manifest.v1",
                "manifest_id": "scanner-fixture",
                "reviewed_at": "2026-09-04",
                "upstream_claims_are_release_evidence": False,
                "artifacts": [
                    {
                        "artifact_id": "fixture-gguf",
                        "variant_id": "fixture",
                        "repository_id": "test/model",
                        "revision": "a" * 40,
                        "source_url": "https://example.invalid/model.gguf",
                        "relative_path": "model.gguf",
                        "sha256": digest,
                        "size_bytes": len(content),
                        "format": "gguf",
                        "quantization": "q4_k_m",
                        "publisher": "test",
                        "license": {
                            "spdx_id": "MIT",
                            "status": "declared",
                            "evidence_kind": "model_card_metadata",
                            "evidence_url": "https://example.invalid/card",
                            "evidence_sha256": "b" * 64,
                            "license_text_present": False,
                        },
                        "activation": "evaluation_only",
                        "reason_codes": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    assert scan(manifest, "fixture-gguf", artifact)["status"] == "passed"
    artifact.write_bytes(b"BAD!fixture")
    assert scan(manifest, "fixture-gguf", artifact)["reason_code"] == "model_artifact_digest_mismatch"
