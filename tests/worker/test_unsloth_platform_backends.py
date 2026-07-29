from __future__ import annotations

from types import SimpleNamespace

import pytest

from worker.training.backends.unsloth import UnslothTrainingBackend
from worker.training.backends.unsloth_audio import UnslothAudioTrainingBackend
from worker.training.backends.unsloth_embedding import _embedding_rows
from worker.training.backends.unsloth_vision import UnslothVisionTrainingBackend
from worker.training.backends.base import TrainingBackendError
from worker.training.vram_admission import VramAdmissionError, VramAdmissionPolicy


def _configuration(**overrides):
    values = {
        "quantization": "4bit",
        "max_sequence_length": 2048,
        "train_batch_size": 1,
        "eval_batch_size": 1,
        "gradient_accumulation_steps": 8,
        "lora_rank": 16,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_rtx3080_policy_reserves_one_gib_and_admits_bounded_weights(tmp_path):
    (tmp_path / "model.safetensors").write_bytes(b"x" * 1024)
    policy = VramAdmissionPolicy(
        profile="rtx3080-safe",
        capacity_bytes=10 * 1024**3,
        reserve_bytes=1024**3,
        max_sequence_length=2048,
        max_batch_size=1,
        max_gradient_accumulation_steps=32,
        max_lora_rank=32,
        required_quantization="4bit",
    )

    result = policy.admit(model_path=tmp_path, configuration=_configuration())

    assert result.admitted is True
    assert result.usable_bytes == 9 * 1024**3
    assert result.as_event()["estimate_only"] is True


def test_rtx3080_policy_rejects_unsafe_sequence_before_model_loading(tmp_path):
    (tmp_path / "model.safetensors").write_bytes(b"x")
    policy = VramAdmissionPolicy(
        profile="rtx3080-safe",
        capacity_bytes=10 * 1024**3,
        reserve_bytes=1024**3,
        max_sequence_length=2048,
        max_batch_size=1,
        max_gradient_accumulation_steps=32,
        max_lora_rank=32,
        required_quantization="4bit",
    )

    with pytest.raises(VramAdmissionError, match="max_sequence_length"):
        policy.admit(model_path=tmp_path, configuration=_configuration(max_sequence_length=4096))


def test_text_backend_rejects_multimodal_model_config(tmp_path):
    (tmp_path / "config.json").write_text('{"model_type":"example","vision_config":{}}', encoding="utf-8")

    with pytest.raises(TrainingBackendError) as raised:
        UnslothTrainingBackend._assert_text_model(tmp_path)

    assert raised.value.code == "unsupported_model_modality"


def test_embedding_rows_are_closed_and_bounded():
    rows = _embedding_rows(
        [
            {"sentence_A": "left", "sentence_B": "right", "label": 0.5},
            {"sentence_A": "a", "sentence_B": "b", "label": 1},
        ]
    )
    assert rows == [
        {"sentence_A": "left", "sentence_B": "right", "label": 0.5},
        {"sentence_A": "a", "sentence_B": "b", "label": 1.0},
    ]


def test_modality_backends_keep_independent_identities():
    assert UnslothVisionTrainingBackend.name == "unsloth_vision"
    assert UnslothAudioTrainingBackend.name == "unsloth_audio"
