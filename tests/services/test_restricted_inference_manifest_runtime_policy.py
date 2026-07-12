from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from agent.services.restricted_inference_contract import RestrictedInferenceOperation
from agent.services.restricted_inference_model_manifest import (
    ENGINE_HUGGINGFACE,
    ENGINE_SENTENCE_TRANSFORMERS,
    FORMAT_SAFETENSORS,
    ROLE_CONFIG,
    ROLE_WEIGHTS,
    SOURCE_LOCAL_SNAPSHOT,
    ModelManifestFile,
    ModelManifestValidationError,
    ModelSnapshotValidator,
    RestrictedModelManifest,
)


def _manifest(**overrides) -> RestrictedModelManifest:
    values = {
        "manifest_id": "manifest-1",
        "model_id": "fixture/model",
        "engine": ENGINE_HUGGINGFACE,
        "model_format": FORMAT_SAFETENSORS,
        "revision": "0123456789abcdef",
        "source_type": SOURCE_LOCAL_SNAPSHOT,
        "license_id": "Apache-2.0",
        "operations": (RestrictedInferenceOperation.CLASSIFY,),
        "files": (
            ModelManifestFile("model.safetensors", hashlib.sha256(b"").hexdigest(), 0, ROLE_WEIGHTS),
            ModelManifestFile("tokenizer.json", hashlib.sha256(b"{}").hexdigest(), 2, ROLE_CONFIG),
        ),
        "tokenizer": "tokenizer.json",
    }
    values.update(overrides)
    return RestrictedModelManifest(**values)


def test_runtime_policy_fields_are_digest_bound() -> None:
    cpu = _manifest(device="cpu", dtype="float32", max_batch_size=4)
    gpu = _manifest(device="cuda:0", dtype="float16", max_batch_size=4)

    assert cpu.digest != gpu.digest
    assert RestrictedModelManifest.from_dict(cpu.to_dict()).digest == cpu.digest


def test_manifest_rejects_unlisted_tokenizer_and_implicit_integer_quantization() -> None:
    with pytest.raises(ModelManifestValidationError) as tokenizer_error:
        _manifest(tokenizer="missing.json")
    assert tokenizer_error.value.reason_code == "invalid_tokenizer"

    with pytest.raises(ModelManifestValidationError) as quantization_error:
        _manifest(dtype="int8", quantization="none")
    assert quantization_error.value.reason_code == "invalid_quantization"


def test_snapshot_license_allowlist_is_enforced_before_model_load(tmp_path: Path) -> None:
    (tmp_path / "model.safetensors").write_bytes(b"")
    (tmp_path / "tokenizer.json").write_bytes(b"{}")
    manifest = _manifest(license_id="Unknown-Proprietary")

    with pytest.raises(ModelManifestValidationError) as error:
        ModelSnapshotValidator().validate(tmp_path, manifest)

    assert error.value.reason_code == "license_not_allowed"


def test_sentence_transformer_bi_encoder_and_cross_encoder_require_separate_manifests() -> None:
    with pytest.raises(ModelManifestValidationError) as error:
        _manifest(
            engine=ENGINE_SENTENCE_TRANSFORMERS,
            operations=(RestrictedInferenceOperation.EMBED, RestrictedInferenceOperation.RERANK),
        )

    assert error.value.reason_code == "mixed_sentence_transformer_modes"
