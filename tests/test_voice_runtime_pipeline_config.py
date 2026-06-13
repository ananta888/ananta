from __future__ import annotations

import pytest

from voice_runtime.config import VoiceRuntimeConfig


def test_voice_runtime_config_reads_pipeline_env(monkeypatch):
    monkeypatch.setenv("VOICE_TRANSCRIPTION_PIPELINE", "confidence_rerun")
    monkeypatch.setenv("VOICE_VAD_BACKEND", "mock")
    monkeypatch.setenv("VOICE_ASR_BACKEND", "whisper_cpp")
    monkeypatch.setenv("VOICE_CONFIDENCE_RERUN_ENABLED", "true")
    monkeypatch.setenv("VOICE_CONFIDENCE_THRESHOLD", "0.42")
    monkeypatch.setenv("VOICE_RERUN_MAX_SEGMENTS", "2")

    config = VoiceRuntimeConfig.from_env()

    assert config.transcription_pipeline == "confidence_rerun"
    assert config.asr_backend == "whisper_cpp"
    assert config.confidence_rerun_enabled is True
    assert config.confidence_threshold == 0.42
    assert config.rerun_max_segments == 2


def test_voice_runtime_config_rejects_unknown_pipeline(monkeypatch):
    monkeypatch.setenv("VOICE_TRANSCRIPTION_PIPELINE", "surprise")

    with pytest.raises(ValueError, match="VOICE_TRANSCRIPTION_PIPELINE"):
        VoiceRuntimeConfig.from_env()


def test_voice_runtime_config_rejects_unknown_vad(monkeypatch):
    monkeypatch.setenv("VOICE_VAD_BACKEND", "surprise")

    with pytest.raises(ValueError, match="VOICE_VAD_BACKEND"):
        VoiceRuntimeConfig.from_env()


def test_voice_runtime_config_default_is_compatible():
    config = VoiceRuntimeConfig()

    assert config.transcription_pipeline == "simple"
    assert config.asr_backend == "mock"
    assert config.backend_fallback_order == ("voxtral", "mock")
