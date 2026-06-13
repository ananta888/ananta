from __future__ import annotations

import subprocess

import pytest

from voice_runtime.backends.router import build_voice_backend_router
from voice_runtime.backends.whisper_cpp import WhisperCppBackend
from voice_runtime.config import VoiceRuntimeConfig


def test_router_falls_back_when_vosk_unavailable():
    config = VoiceRuntimeConfig(backend_fallback_order=("vosk", "mock"), vosk_model_path=None)
    router = build_voice_backend_router(config)

    result = router.transcribe(filename="sample.webm", content=b"audio")

    assert result.raw_backend == "mock"
    assert "fallback_backend:mock" in result.warnings


def test_whisper_cpp_builds_argv_without_shell_string():
    backend = WhisperCppBackend(
        binary="/usr/local/bin/whisper-cli",
        model_path="/models/base.bin",
        extra_args=("--best-of", "1"),
    )

    argv = backend.build_argv(input_path="/tmp/in.wav", output_path="/tmp/out.json", language="de")

    assert isinstance(argv, list)
    assert argv[:5] == ["/usr/local/bin/whisper-cli", "-m", "/models/base.bin", "-f", "/tmp/in.wav"]
    assert "-l" in argv
    assert "--best-of" in argv


def test_whisper_cpp_maps_subprocess_timeout(monkeypatch):
    backend = WhisperCppBackend(binary="/bin/echo", model_path="/models/base.bin", timeout_sec=1)

    def raise_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=1)

    monkeypatch.setattr(subprocess, "run", raise_timeout)

    with pytest.raises(TimeoutError, match="whisper.cpp backend timeout"):
        backend.transcribe(filename="sample.wav", content=b"audio")


def test_whisper_cpp_parses_json_segments():
    backend = WhisperCppBackend(binary="/bin/echo", model_path="/models/base.bin")

    result = backend.parse_json_output(
        '{"language":"en","transcription":[{"from":0.0,"to":1.25,"text":"hello","confidence":0.8}]}'
    )

    assert result.text == "hello"
    assert result.segments[0].start_ms == 0
    assert result.segments[0].end_ms == 1250
    assert result.confidence == 0.8
