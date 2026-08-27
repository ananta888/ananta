from __future__ import annotations

import json
from pathlib import Path

import pytest

from worker.training.backend_artifact_normalizer import BackendArtifactNormalizer
from worker.training.backend_checkpoint_adapter import require_compatible_checkpoint
from worker.training.backends.base import TrainingBackendError


def _binding() -> dict[str, str]:
    return {
        "attempt_id": "attempt-1",
        "backend": "axolotl",
        "backend_version": "0.18.0",
        "base_model_sha256": "a" * 64,
        "configuration_sha256": "b" * 64,
        "dataset_sha256": "c" * 64,
        "job_id": "job-1",
    }


def test_artifacts_are_hash_bound_to_one_normalized_manifest(tmp_path: Path) -> None:
    weights = tmp_path / "adapter_model.safetensors"
    config = tmp_path / "adapter_config.json"
    weights.write_bytes(b"safe")
    config.write_text("{}", encoding="utf-8")
    result = BackendArtifactNormalizer().normalize(
        artifact_root=tmp_path,
        candidates=(weights, config),
        binding=_binding(),
    )
    manifest = json.loads(result.manifest_path.read_text())
    assert manifest["base_model_sha256"] == "a" * 64
    assert {item["name"] for item in manifest["artifacts"]} == {
        "adapter_config.json",
        "adapter_model.safetensors",
    }
    assert len(result.manifest_sha256) == 64


def test_missing_symlinked_and_out_of_root_artifacts_are_rejected(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-adapter.safetensors"
    outside.write_bytes(b"unsafe")
    symlink = tmp_path / "adapter_model.safetensors"
    symlink.symlink_to(outside)
    config = tmp_path / "adapter_config.json"
    config.write_text("{}", encoding="utf-8")
    with pytest.raises(TrainingBackendError) as error:
        BackendArtifactNormalizer().normalize(
            artifact_root=tmp_path,
            candidates=(symlink, config, outside),
            binding=_binding(),
        )
    assert error.value.code == "artifact_invalid"


def test_checkpoint_requires_exact_backend_and_provenance_binding() -> None:
    expected = {
        "backend": "axolotl",
        "backend_version": "0.18.0",
        "base_model_sha256": "a" * 64,
        "configuration_sha256": "b" * 64,
        "dataset_sha256": "c" * 64,
        "format": "safetensors-adapter-v1",
    }
    require_compatible_checkpoint(expected, dict(expected))
    changed = dict(expected, backend="llamafactory")
    with pytest.raises(TrainingBackendError) as error:
        require_compatible_checkpoint(changed, expected)
    assert error.value.code == "checkpoint_incompatible"
