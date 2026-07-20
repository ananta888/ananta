from __future__ import annotations

import pytest

from worker.speech_training.backend import SpeechTrainingBackendError
from worker.speech_training.backend_registry import SpeechTrainingBackendRegistry
from worker.speech_training.backends import MockSpeechTrainingBackend


def test_registry_is_startup_frozen_and_allowlist_only() -> None:
    registry = SpeechTrainingBackendRegistry([MockSpeechTrainingBackend()])
    assert registry.names == ("mock",)
    assert registry.capabilities() == {"mock": {"available": True, "reason_code": None}}

    with pytest.raises(TypeError):
        registry._backends["dynamic"] = MockSpeechTrainingBackend()  # type: ignore[index]
    with pytest.raises(SpeechTrainingBackendError) as captured:
        registry.require("openvoice_v2")
    assert captured.value.reason_code == "speech_backend_not_admitted"


def test_duplicate_or_payload_named_backend_cannot_replace_registry() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        SpeechTrainingBackendRegistry([MockSpeechTrainingBackend(), MockSpeechTrainingBackend()])

    class DynamicBackend(MockSpeechTrainingBackend):
        name = "peer://dynamic/backend"

    with pytest.raises(ValueError, match="not contract-allowlisted"):
        SpeechTrainingBackendRegistry([DynamicBackend()])
