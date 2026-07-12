from __future__ import annotations

import io
import wave
from pathlib import Path
from typing import Callable

import pytest

from voice_runtime.backends.router import build_voice_backend_router
from voice_runtime.backends.voxtral import VoxtralBackend
from voice_runtime.backends.whisper_cpp import WhisperCppBackend
from voice_runtime.config import VoiceRuntimeConfig
from voice_runtime.preprocessing.audio_decode import ProcessResult


def _wav_bytes() -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16_000)
        output.writeframes(b"\x00\x00" * 1_600)
    return buffer.getvalue()


class _TimeoutRunner:
    def run(
        self,
        _argv: list[str],
        *,
        input_payload: bytes,
        max_stdout_bytes: int,
        timeout_seconds: float,
        cwd: Path,
        cancellation_check: Callable[[], None] | None = None,
    ) -> ProcessResult:
        del input_payload, max_stdout_bytes, timeout_seconds, cwd, cancellation_check
        raise TimeoutError("bounded subprocess timed out")


def test_router_falls_back_when_vosk_unavailable():
    config = VoiceRuntimeConfig(backend_fallback_order=("vosk", "mock"), vosk_model_path=None)
    router = build_voice_backend_router(config)

    result = router.transcribe(filename="sample.wav", content=_wav_bytes())

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


def test_whisper_cpp_maps_bounded_runner_timeout(tmp_path):
    model_path = tmp_path / "base.bin"
    model_path.write_bytes(b"model")
    backend = WhisperCppBackend(
        binary="/bin/echo",
        model_path=str(model_path),
        timeout_sec=1,
        process_runner=_TimeoutRunner(),
    )

    with pytest.raises(TimeoutError, match="whisper.cpp backend timeout"):
        backend.transcribe(filename="sample.wav", content=_wav_bytes())


def test_voxtral_maps_bounded_runner_timeout(tmp_path):
    model_path = tmp_path / "voxtral.gguf"
    model_path.write_bytes(b"model")
    backend = VoxtralBackend(
        model="voxtral",
        fallback_model="mock",
        model_path=str(model_path),
        runner_path="/bin/echo",
        timeout_sec=1,
        process_runner=_TimeoutRunner(),
    )

    with pytest.raises(TimeoutError, match="Voxtral backend timed out"):
        backend.transcribe(filename="sample.wav", content=_wav_bytes())


def test_whisper_cpp_parses_json_segments():
    backend = WhisperCppBackend(binary="/bin/echo", model_path="/models/base.bin")

    result = backend.parse_json_output(
        '{"language":"en","transcription":[{"from":0.0,"to":1.25,"text":"hello","confidence":0.8}]}'
    )

    assert result.text == "hello"
    assert result.segments[0].start_ms == 0
    assert result.segments[0].end_ms == 1250
    assert result.confidence == 0.8
