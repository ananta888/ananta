from __future__ import annotations

import pytest

from voice_runtime.app import create_app
from voice_runtime.config import VoiceRuntimeConfig


def test_voice_runtime_config_reads_pipeline_env(monkeypatch):
    monkeypatch.setenv("VOICE_TRANSCRIPTION_PIPELINE", "confidence_rerun")
    monkeypatch.setenv("VOICE_VAD_BACKEND", "mock")
    monkeypatch.setenv("VOICE_ASR_BACKEND", "whisper_cpp")
    monkeypatch.setenv("VOICE_CONFIDENCE_RERUN_ENABLED", "true")
    monkeypatch.setenv("VOICE_CONFIDENCE_THRESHOLD", "0.42")
    monkeypatch.setenv("VOICE_RERUN_MAX_SEGMENTS", "2")
    monkeypatch.setenv("VOICE_RERUN_MAX_AUDIO_MS", "1234")
    monkeypatch.setenv("VOICE_RESOURCE_MAX_RAM_MB", "4096")
    monkeypatch.setenv("VOICE_RESOURCE_MAX_VRAM_MB", "2048")
    monkeypatch.setenv("VOICE_RESOURCE_MAX_CONCURRENT_BACKENDS", "3")
    monkeypatch.setenv("VOICE_RESOURCE_MAX_AUDIO_SECONDS", "600")
    monkeypatch.setenv("VOICE_RESOURCE_MAX_QUEUE_DEPTH", "5")
    monkeypatch.setenv("VOICE_SILERO_VAD_THRESHOLD", "0.73")
    monkeypatch.setenv("VOICE_STREAM_TIMEOUT_SEC", "275")

    config = VoiceRuntimeConfig.from_env()

    assert config.transcription_pipeline == "confidence_rerun"
    assert config.asr_backend == "whisper_cpp"
    assert config.confidence_rerun_enabled is True
    assert config.confidence_threshold == 0.42
    assert config.rerun_max_segments == 2
    assert config.rerun_max_audio_ms == 1234
    assert config.resource_max_ram_mb == 4096
    assert config.resource_max_vram_mb == 2048
    assert config.resource_max_concurrent_backends == 3
    assert config.resource_max_audio_seconds == 600
    assert config.resource_max_queue_depth == 5
    assert config.silero_vad_threshold == 0.73
    assert config.stream_timeout_sec == 275


def test_blank_whisper_gpu_switch_preserves_legacy_positive_layers_alias(monkeypatch):
    monkeypatch.setenv("VOICE_WHISPER_CPP_GPU_LAYERS", "12")
    monkeypatch.setenv("VOICE_WHISPER_CPP_GPU_ENABLED", "")

    config = VoiceRuntimeConfig.from_env()

    assert config.whisper_cpp_gpu_layers == 12
    assert config.whisper_cpp_gpu_enabled is None


def test_voice_runtime_config_rejects_unknown_pipeline(monkeypatch):
    monkeypatch.setenv("VOICE_TRANSCRIPTION_PIPELINE", "surprise")

    with pytest.raises(ValueError, match="VOICE_TRANSCRIPTION_PIPELINE"):
        VoiceRuntimeConfig.from_env()


def test_voice_runtime_config_rejects_unknown_vad(monkeypatch):
    monkeypatch.setenv("VOICE_VAD_BACKEND", "surprise")

    with pytest.raises(ValueError, match="VOICE_VAD_BACKEND"):
        VoiceRuntimeConfig.from_env()


def test_voice_runtime_config_rejects_invalid_silero_threshold(monkeypatch):
    monkeypatch.setenv("VOICE_SILERO_VAD_THRESHOLD", "1.01")

    with pytest.raises(ValueError, match="VOICE_SILERO_VAD_THRESHOLD"):
        VoiceRuntimeConfig.from_env()


def test_voice_runtime_config_rejects_invalid_stream_timeout():
    with pytest.raises(ValueError, match="VOICE_STREAM_TIMEOUT_SEC"):
        VoiceRuntimeConfig(stream_timeout_sec=3_601).validate()


def test_voice_runtime_config_default_is_compatible():
    config = VoiceRuntimeConfig()

    assert config.transcription_pipeline == "simple"
    assert config.asr_backend == "mock"
    assert config.backend_fallback_order == ("voxtral", "mock")
    assert config.stream_timeout_sec == 300


def test_legacy_pipeline_and_fallback_order_project_to_canonical_runtime_axes(monkeypatch):
    monkeypatch.setenv("VOICE_TRANSCRIPTION_PIPELINE", "realtime_streaming")
    monkeypatch.setenv("VOICE_BACKEND_FALLBACK_ORDER", "vosk,whisper_cpp,mock")
    monkeypatch.setenv("VOICE_ENABLE_STREAMING", "true")

    config = VoiceRuntimeConfig.from_env()

    assert config.transcription_pipeline == "realtime_streaming"
    assert config.backend_fallback_order == ("vosk", "whisper_cpp", "mock")
    assert config.transport_mode == "stream"
    assert config.recognition_strategy == "single"
    assert config.primary_backend == "vosk"
    assert config.secondary_backends == ("whisper_cpp", "mock")


def test_explicit_canonical_runtime_backends_override_legacy_fallback_projection(monkeypatch):
    monkeypatch.setenv("VOICE_BACKEND_FALLBACK_ORDER", "vosk,whisper_cpp,mock")
    monkeypatch.setenv("VOICE_PRIMARY_BACKEND", "faster_whisper")
    monkeypatch.setenv("VOICE_SECONDARY_BACKENDS", "voxtral")

    config = VoiceRuntimeConfig.from_env()

    assert config.backend_fallback_order == ("vosk", "whisper_cpp", "mock")
    assert config.primary_backend == "faster_whisper"
    assert config.secondary_backends == ("voxtral",)


def test_app_composition_injects_the_configured_resource_ceiling():
    config = VoiceRuntimeConfig(
        backend_fallback_order=("mock",),
        max_queue_depth=7,
        max_audio_duration_sec=180,
        resource_max_ram_mb=321,
        resource_max_vram_mb=123,
        resource_max_concurrent_backends=3,
        resource_max_audio_seconds=90,
        resource_max_queue_depth=5,
    )

    app = create_app(config)
    executor = app.config["voice_runtime_pipeline"]._candidate_executor
    budget = executor._admission._runtime_budget

    assert budget.as_dict() == {
        "max_ram_bytes": 321 * 1024 * 1024,
        "max_vram_bytes": 123 * 1024 * 1024,
        "max_concurrent_backends": 3,
        "max_audio_ms": 90_000,
        "max_queue_depth": 5,
    }


def test_app_composition_uses_separate_total_stream_timeout():
    app = create_app(VoiceRuntimeConfig(
        enable_streaming=True,
        stream_timeout_sec=245,
        backend_fallback_order=("mock",),
    ))

    manager = app.config["voice_runtime_stream_manager"]

    assert manager._default_deadline_seconds == 245
