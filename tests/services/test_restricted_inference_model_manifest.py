from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from agent.services.restricted_inference_contract import RestrictedInferenceOperation
from agent.services.restricted_inference_model_manifest import (
    ENGINE_HUGGINGFACE,
    FORMAT_SAFETENSORS,
    MANIFEST_SCHEMA_VERSION,
    ROLE_CONFIG,
    ROLE_WEIGHTS,
    SOURCE_LOCAL_SNAPSHOT,
    ModelManifestFile,
    ModelManifestValidationError,
    ModelSnapshotValidator,
    RestrictedModelManifest,
    SnapshotValidationPolicy,
)


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _snapshot(tmp_path: Path) -> tuple[Path, RestrictedModelManifest]:
    root = tmp_path / "snapshot"
    root.mkdir()
    weights = b"safe tensor fixture"
    config = b'{"model_type":"fixture"}'
    (root / "model.safetensors").write_bytes(weights)
    (root / "config.json").write_bytes(config)
    manifest = RestrictedModelManifest(
        schema_version=MANIFEST_SCHEMA_VERSION,
        manifest_id="fixture-manifest-v1",
        model_id="org/fixture-model",
        engine=ENGINE_HUGGINGFACE,
        model_format=FORMAT_SAFETENSORS,
        revision="0123456789abcdef",
        source_type=SOURCE_LOCAL_SNAPSHOT,
        license_id="Apache-2.0",
        operations=(
            RestrictedInferenceOperation.CLASSIFY,
            RestrictedInferenceOperation.EXTRACT_FEATURES,
        ),
        files=(
            ModelManifestFile("model.safetensors", _digest(weights), len(weights), ROLE_WEIGHTS),
            ModelManifestFile("config.json", _digest(config), len(config), ROLE_CONFIG),
        ),
    )
    return root, manifest


def test_manifest_roundtrip_and_digest_are_deterministic(tmp_path: Path) -> None:
    root, manifest = _snapshot(tmp_path)
    raw = manifest.to_dict()
    raw["metadata"] = {"provenance": {"publisher": "fixture"}}
    restored = RestrictedModelManifest.from_dict(raw)

    assert RestrictedModelManifest.from_dict(restored.to_dict()).digest == restored.digest
    with pytest.raises(TypeError):
        restored.metadata["provenance"]["publisher"] = "mutated"  # type: ignore[index]
    verified = ModelSnapshotValidator().validate(root, restored)
    assert verified.manifest_digest == restored.digest
    assert verified.total_size_bytes == manifest.declared_size_bytes
    assert set(verified.file_digests) == {"model.safetensors", "config.json"}


@pytest.mark.parametrize("revision", ["", "main", "LATEST", "master"])
def test_manifest_rejects_unpinned_revision(tmp_path: Path, revision: str) -> None:
    _, manifest = _snapshot(tmp_path)
    raw = manifest.to_dict()
    raw["revision"] = revision

    with pytest.raises(ModelManifestValidationError) as exc_info:
        RestrictedModelManifest.from_dict(raw)

    assert exc_info.value.reason_code == "unpinned_revision"


def test_manifest_rejects_remote_code_and_unsafe_weights_format(tmp_path: Path) -> None:
    _, manifest = _snapshot(tmp_path)
    raw = manifest.to_dict()
    raw["trust_remote_code"] = True
    with pytest.raises(ModelManifestValidationError) as remote_error:
        RestrictedModelManifest.from_dict(raw)
    assert remote_error.value.reason_code == "remote_code_forbidden"

    raw = manifest.to_dict()
    raw["files"][0]["path"] = "model.pkl"
    with pytest.raises(ModelManifestValidationError) as format_error:
        RestrictedModelManifest.from_dict(raw)
    assert format_error.value.reason_code == "unsafe_model_format"


def test_manifest_rejects_path_traversal_and_unknown_fields(tmp_path: Path) -> None:
    _, manifest = _snapshot(tmp_path)
    raw = manifest.to_dict()
    raw["files"][0]["path"] = "../model.safetensors"
    with pytest.raises(ModelManifestValidationError) as path_error:
        RestrictedModelManifest.from_dict(raw)
    assert path_error.value.reason_code == "unsafe_manifest_path"

    raw = manifest.to_dict()
    raw["download_token"] = "secret"
    with pytest.raises(ModelManifestValidationError) as field_error:
        RestrictedModelManifest.from_dict(raw)
    assert field_error.value.reason_code == "unknown_manifest_field"


def test_snapshot_rejects_hash_and_size_mismatch(tmp_path: Path) -> None:
    root, manifest = _snapshot(tmp_path)
    (root / "model.safetensors").write_bytes(b"tampered tensor fixture")

    with pytest.raises(ModelManifestValidationError) as exc_info:
        ModelSnapshotValidator().validate(root, manifest)

    assert exc_info.value.reason_code in {"hash_mismatch", "size_mismatch"}


def test_snapshot_rejects_unlisted_file(tmp_path: Path) -> None:
    root, manifest = _snapshot(tmp_path)
    (root / "payload.py").write_text("raise SystemExit", encoding="utf-8")

    with pytest.raises(ModelManifestValidationError) as exc_info:
        ModelSnapshotValidator().validate(root, manifest)

    assert exc_info.value.reason_code == "unlisted_snapshot_file"


def test_snapshot_rejects_symlink(tmp_path: Path) -> None:
    root, manifest = _snapshot(tmp_path)
    target = tmp_path / "outside.safetensors"
    target.write_bytes(b"outside")
    (root / "model.safetensors").unlink()
    (root / "model.safetensors").symlink_to(target)

    with pytest.raises(ModelManifestValidationError) as exc_info:
        ModelSnapshotValidator().validate(root, manifest)

    assert exc_info.value.reason_code == "snapshot_symlink"


def test_snapshot_rejects_hardlink(tmp_path: Path) -> None:
    root, manifest = _snapshot(tmp_path)
    linked = tmp_path / "linked.safetensors"
    os.link(root / "model.safetensors", linked)

    with pytest.raises(ModelManifestValidationError) as exc_info:
        ModelSnapshotValidator().validate(root, manifest)

    assert exc_info.value.reason_code == "snapshot_hardlink"


def test_snapshot_enforces_declared_size_budget(tmp_path: Path) -> None:
    root, manifest = _snapshot(tmp_path)
    validator = ModelSnapshotValidator(SnapshotValidationPolicy(max_file_bytes=8, max_total_bytes=16))

    with pytest.raises(ModelManifestValidationError) as exc_info:
        validator.validate(root, manifest)

    assert exc_info.value.reason_code in {"file_too_large", "snapshot_too_large"}
