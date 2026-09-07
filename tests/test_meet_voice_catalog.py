"""Known voice IDs bind immutable assets and speaker choices, never grants."""

import json
import sys
from dataclasses import FrozenInstanceError
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

from ananta_contracts.meet_speech import (
    CONFIG_SHA256,
    MODEL_NAME,
    MODEL_SHA256,
    speech_profile,
    validate_speech_profile,
)
from ananta_contracts.meet_voice_catalog import DEFAULT_MODEL, DEFAULT_VOICE_ID, EMOTIONAL_MODEL, PRESETS, voice_preset
from worker.meet_media import piper_assets, piper_speech


def emotional(name="neutral"):
    return speech_profile(voice_id="piper.de_DE.thorsten_emotional.medium." + name, max_seconds=5)


def test_immutable_catalog_preserves_legacy_exports_and_closed_default_projection():
    assert len(PRESETS) == 9 and len({item.voice_id for item in PRESETS}) == 9
    assert (DEFAULT_MODEL.name, DEFAULT_MODEL.model_sha256, DEFAULT_MODEL.config_sha256) == (
        MODEL_NAME,
        MODEL_SHA256,
        CONFIG_SHA256,
    )
    assert speech_profile()["voice_id"] == DEFAULT_VOICE_ID and DEFAULT_MODEL.model_max_bytes == 70_000_000
    assert set(speech_profile()) == {
        "schema",
        "voice_id",
        "language",
        "model_revision",
        "model_sha256",
        "config_sha256",
        "sample_rate",
        "channels",
        "sample_format",
        "max_seconds",
    }
    with pytest.raises(FrozenInstanceError):
        PRESETS[1].speaker_id = 100
    with pytest.raises(FrozenInstanceError):
        EMOTIONAL_MODEL.model_max_bytes = 100_000_000


@pytest.mark.parametrize("name,speaker", EMOTIONAL_MODEL.speakers)
def test_every_expression_is_bound_to_its_exact_known_model_and_upstream_speaker(name, speaker):
    value = emotional(name)
    assert validate_speech_profile(value) == value
    selected = voice_preset(value["voice_id"])
    assert selected.speaker_id == speaker and selected.model is EMOTIONAL_MODEL
    assert value["sample_rate"] == 22050 and value["language"] == "de-DE"
    assert set(value) == set(speech_profile())  # No arbitrary caller speaker index or asset path.


@pytest.mark.parametrize(
    "patch",
    [
        {"voice_id": "unknown"},
        {"voice_id": True},
        {"voice_id": ["neutral"]},
        {"model_sha256": MODEL_SHA256},
        {"config_sha256": CONFIG_SHA256},
        {"speaker_id": 4},
        {"model_path": "/models/other.onnx"},
        {"language": "en-US"},
        {"model_revision": "main"},
    ],
)
def test_unknown_or_mixed_voice_fields_never_select_another_model(patch):
    with pytest.raises(ValueError):
        validate_speech_profile(emotional() | patch)


def test_loader_uses_only_catalog_basename_under_explicit_operator_directory(monkeypatch, tmp_path):
    monkeypatch.delenv("MEET_PIPER_MODEL", raising=False)
    monkeypatch.setenv("MEET_PIPER_MODELS_DIR", str(tmp_path))
    read = Mock(return_value=b"pinned-snapshot")
    monkeypatch.setattr(piper_assets, "read_pinned_file", read)
    assert piper_assets.load_pinned_assets(emotional()) == (b"pinned-snapshot", b"pinned-snapshot")
    calls = read.call_args_list
    assert str(calls[0].args[0]) == str(tmp_path / EMOTIONAL_MODEL.name)
    assert calls[0].kwargs == {"sha256": EMOTIONAL_MODEL.model_sha256, "maximum": 76_745_905}
    assert calls[1].kwargs == {"sha256": EMOTIONAL_MODEL.config_sha256, "maximum": 5031}
    read.reset_mock()
    monkeypatch.setenv("MEET_PIPER_MODELS_DIR", "relative")
    with pytest.raises(ValueError, match="path_invalid"):
        piper_assets.load_pinned_assets(emotional())
    read.assert_not_called()


def test_fixed_legacy_model_path_does_not_silently_become_a_voice_directory(monkeypatch, tmp_path):
    monkeypatch.delenv("MEET_PIPER_MODELS_DIR", raising=False)
    monkeypatch.setenv("MEET_PIPER_MODEL", str(tmp_path / "operator-fixed.onnx"))
    read = Mock(return_value=b"snapshot")
    monkeypatch.setattr(piper_assets, "read_pinned_file", read)
    with pytest.raises(ValueError, match="preset_directory_required"):
        piper_assets.load_pinned_assets(emotional())
    read.assert_not_called()
    piper_assets.load_pinned_assets()
    assert str(read.call_args_list[0].args[0]) == str(tmp_path / "operator-fixed.onnx")


@pytest.mark.parametrize("change", ["missing", "count", "boolean", "map", "unknown", "list"])
def test_wrong_speaker_configuration_fails_before_any_model_parser(monkeypatch, change):
    configuration = {"num_speakers": 8, "speaker_id_map": dict(EMOTIONAL_MODEL.speakers)}
    if change == "missing":
        configuration.pop("speaker_id_map")
    elif change == "count":
        configuration["num_speakers"] = 7
    elif change == "boolean":
        configuration["speaker_id_map"]["amused"] = False
    elif change == "map":
        configuration["speaker_id_map"]["neutral"] = 0
    elif change == "unknown":
        configuration["speaker_id_map"]["foreign"] = 8
    else:
        configuration = []
    ort, parser, voice = Mock(), Mock(), Mock()
    monkeypatch.setitem(sys.modules, "onnxruntime", ort)
    monkeypatch.setitem(sys.modules, "piper", SimpleNamespace(PiperVoice=voice))
    monkeypatch.setitem(sys.modules, "piper.config", SimpleNamespace(PiperConfig=parser))
    monkeypatch.setattr(piper_speech, "load_pinned_assets", lambda _: (b"model", json.dumps(configuration).encode()))
    with pytest.raises(ValueError, match="speaker_map_invalid"):
        piper_speech.load_cuda_voice(emotional())
    ort.InferenceSession.assert_not_called()
    ort.preload_dlls.assert_not_called()
    parser.from_dict.assert_not_called()


@pytest.mark.parametrize("name,speaker", [("neutral", 4), ("whisper", 7)])
def test_synthesis_receives_only_catalog_owned_speaker_selection(monkeypatch, name, speaker):
    configuration = Mock(side_effect=lambda **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setitem(sys.modules, "piper", SimpleNamespace(SynthesisConfig=configuration))
    chunk = SimpleNamespace(
        sample_rate=22050, sample_width=2, sample_channels=1, audio_float_array=np.array([0.1, -0.1], dtype=np.float32)
    )
    voice = Mock()
    voice.synthesize.return_value = iter([chunk])
    source = piper_speech.PiperSpeechSource(loader=lambda: voice, profile=emotional(name))
    assert len(b"".join(source.synthesize("Synthetischer Test", max_samples=10, require_current=lambda: None))) == 4
    configuration.assert_called_once_with(speaker_id=speaker)
    assert voice.synthesize.call_args.kwargs["syn_config"].speaker_id == speaker
