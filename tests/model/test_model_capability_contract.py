from __future__ import annotations

import pytest

from ananta_contracts.model_capability import ModelCapability, ModelStatus


def _payload() -> dict:
    return {
        "schema_version": "ananta.model-capability.v1",
        "id": "whisper-base-de",
        "engine": "whisper_cpp",
        "revision": "sha256:model",
        "tasks": ["transcription"],
        "languages": ["de", "en"],
        "device": "cpu",
        "quantization": "q5_1",
        "license": "MIT",
        "status": "ready",
        "manifest_digest": "sha256:manifest",
        "extensions": {"voice": {"batch": True, "streaming": False}},
    }


def test_model_capability_roundtrip_is_versioned_and_typed():
    capability = ModelCapability.from_mapping(_payload())

    assert capability.status is ModelStatus.READY
    assert capability.as_dict() == _payload()


def test_model_capability_separates_extension_interfaces():
    payload = _payload()
    payload["extensions"] = {"everything": {"mutable_registry": True}}

    with pytest.raises(ValueError, match="unknown capability extensions"):
        ModelCapability.from_mapping(payload)


def test_model_capability_rejects_unversioned_or_invalid_status():
    payload = _payload()
    payload["schema_version"] = ""
    with pytest.raises(ValueError, match="unsupported"):
        ModelCapability.from_mapping(payload)

    payload = _payload()
    payload["status"] = "loaded"
    with pytest.raises(ValueError, match="ready, degraded, or unavailable"):
        ModelCapability.from_mapping(payload)
