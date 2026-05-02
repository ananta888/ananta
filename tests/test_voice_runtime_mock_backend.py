from __future__ import annotations

from io import BytesIO

import pytest

from voice_runtime.app import create_app
from voice_runtime.backends.mock import MockVoiceBackend
from voice_runtime.backends.router import build_voice_backend_router
from voice_runtime.config import VoiceRuntimeConfig


def test_mock_voice_backend_transcribe_is_deterministic():
    backend = MockVoiceBackend(model="mock-test")
    first = backend.transcribe(filename="sample.webm", content=b"audio-bytes", language="de")
    second = backend.transcribe(filename="sample.webm", content=b"audio-bytes", language="de")

    assert first.text == second.text
    assert first.text.startswith("mock transcript (sample.webm)")
    assert first.language == "de"
    assert first.model == "mock-test"


def test_mock_voice_backend_audio_chat_returns_intent_when_context_present():
    backend = MockVoiceBackend()
    result = backend.audio_chat(filename="sample.webm", content=b"audio-bytes", context={"mode": "command"})

    assert result.text
    assert result.transcript == result.text
    assert (result.tool_intent or {}).get("type") == "voice_command"


def test_mock_voice_backend_modes_timeout_and_unavailable(monkeypatch):
    backend = MockVoiceBackend()

    monkeypatch.setenv("VOICE_MOCK_MODE", "timeout")
    with pytest.raises(TimeoutError):
        backend.transcribe(filename="sample.webm", content=b"audio-bytes")

    monkeypatch.setenv("VOICE_MOCK_MODE", "unavailable")
    with pytest.raises(RuntimeError):
        backend.transcribe(filename="sample.webm", content=b"audio-bytes")


def test_router_falls_back_to_mock_and_emits_warning(monkeypatch):
    monkeypatch.setenv("VOICE_VOXTRAL_MODE", "unavailable")
    config = VoiceRuntimeConfig(
        backend_fallback_order=("voxtral", "mock"),
        model="voxtral",
        fallback_model="whisper-small",
    )
    router = build_voice_backend_router(config)

    result = router.transcribe(filename="sample.webm", content=b"audio-bytes")

    assert result.model == "mock-voxtral"
    assert "fallback_backend:mock" in result.warnings


def test_voice_runtime_http_health_models_and_transcription():
    app = create_app(
        VoiceRuntimeConfig(
            backend_fallback_order=("mock",),
            backend="mock",
            model="voxtral",
            max_audio_mb=1,
        )
    )
    client = app.test_client()

    health = client.get("/health")
    assert health.status_code == 200
    assert health.json.get("ok") is True

    models = client.get("/v1/models")
    assert models.status_code == 200
    assert isinstance(models.json.get("models"), list)

    transcribe = client.post(
        "/v1/audio/transcriptions",
        data={"file": (BytesIO(b"audio-bytes"), "sample.webm")},
        content_type="multipart/form-data",
    )
    assert transcribe.status_code == 200
    assert isinstance(transcribe.json.get("text"), str)


def test_voice_runtime_http_rejects_oversized_file():
    app = create_app(
        VoiceRuntimeConfig(
            backend_fallback_order=("mock",),
            backend="mock",
            model="voxtral",
            max_audio_mb=1,
        )
    )
    client = app.test_client()
    payload = b"x" * (2 * 1024 * 1024)

    response = client.post(
        "/v1/audio/transcriptions",
        data={"file": (BytesIO(payload), "too-large.webm")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 413
    assert (response.json.get("error") or {}).get("code") == "validation.file_too_large"
