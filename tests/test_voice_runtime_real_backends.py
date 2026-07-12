from __future__ import annotations

import io
import json
import stat
import sys
import time
import types
import wave
from pathlib import Path
from typing import Callable

import pytest

from voice_runtime.backends.base import TranscriptionResult
from voice_runtime.backends.faster_whisper import FasterWhisperBackend
from voice_runtime.backends.vosk_backend import VoskBackend
from voice_runtime.backends.voxtral import VoxtralBackend
from voice_runtime.backends.whisper_cpp import WhisperCppBackend
from voice_runtime.errors import BackendCancelledError
from voice_runtime.execution_control import BackendCancellationToken
from voice_runtime.preprocessing.audio_decode import ProcessResult


def _wav_bytes(*, duration_ms: int = 100) -> bytes:
    frame_count = duration_ms * 16_000 // 1000
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16_000)
        output.writeframes(b"\x00\x00" * frame_count)
    return buffer.getvalue()


def _write_python_executable(path: Path, body: str) -> Path:
    path.write_text(f"#!{sys.executable}\n{body}", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


class _FakeVoskRecognizer:
    def __init__(self, _model, sample_rate):
        assert sample_rate == 16_000
        self.words_enabled = False

    def SetWords(self, enabled):
        self.words_enabled = enabled

    def AcceptWaveform(self, _content):
        return False

    def Result(self):
        return "{}"

    def PartialResult(self):
        return json.dumps({"partial": "hallo"})

    def FinalResult(self):
        assert self.words_enabled is True
        return json.dumps(
            {
                "text": "hallo welt",
                "result": [
                    {"word": "hallo", "start": 0.0, "end": 0.04, "conf": 0.8},
                    {"word": "welt", "start": 0.04, "end": 0.1, "conf": 0.6},
                ],
            }
        )


def test_vosk_runs_real_recognizer_contract_and_loads_model_once(tmp_path):
    model_path = tmp_path / "vosk-model"
    model_path.mkdir()
    loaded_paths = []
    def load_model(path):
        loaded_paths.append(path)
        return object()

    module = types.SimpleNamespace(Model=load_model, KaldiRecognizer=_FakeVoskRecognizer)
    backend = VoskBackend(model_path=str(model_path), vosk_module=module)

    first = backend.transcribe(filename="sample.wav", content=_wav_bytes(), language="de")
    second = backend.transcribe(filename="sample.wav", content=_wav_bytes(), language="de")

    assert first.text == "hallo welt"
    assert first.language == "de"
    assert [(segment.text, segment.start_ms, segment.end_ms) for segment in first.segments] == [
        ("hallo", 0, 40),
        ("welt", 40, 100),
    ]
    assert first.confidence == pytest.approx(0.7)
    assert second.text == first.text
    assert loaded_paths == [str(model_path.resolve())]


def test_vosk_never_resolves_missing_model_as_remote_identifier(tmp_path):
    module = types.SimpleNamespace(Model=lambda _path: object(), KaldiRecognizer=_FakeVoskRecognizer)
    backend = VoskBackend(model_path=str(tmp_path / "missing"), vosk_module=module)

    with pytest.raises(RuntimeError, match="does not exist"):
        backend.transcribe(filename="sample.wav", content=_wav_bytes())


def test_vosk_incremental_recognizer_emits_partial_and_final(tmp_path):
    model_path = tmp_path / "vosk-model"
    model_path.mkdir()
    module = types.SimpleNamespace(
        Model=lambda _path: object(),
        KaldiRecognizer=_FakeVoskRecognizer,
    )
    backend = VoskBackend(model_path=str(model_path), vosk_module=module)
    recognizer = backend.create_incremental_recognizer(
        filename="stream.pcm",
        language="de",
        max_bytes=10_000,
    )

    partial = recognizer.accept(b"\x00\x00" * 1_600)
    result = recognizer.finish()

    assert partial == "hallo"
    assert result.text == "hallo welt"
    assert result.duration_ms == 100
    recognizer.close()


def test_whisper_cpp_rejects_unallowlisted_arguments():
    with pytest.raises(ValueError, match="unsupported whisper.cpp argument"):
        WhisperCppBackend(
            binary="/bin/echo",
            model_path="/models/base.bin",
            extra_args=("-f", "/etc/passwd"),
        )

    with pytest.raises(ValueError, match="must be finite"):
        WhisperCppBackend(
            binary="/bin/echo",
            model_path="/models/base.bin",
            extra_args=("--temperature", "nan"),
        )


def test_whisper_cpp_uses_bounded_real_process_and_parses_json_output_file(tmp_path):
    model_path = tmp_path / "base.bin"
    model_path.write_bytes(b"model")
    payload = {
        "language": "de",
        "transcription": [
            {
                "timestamps": {"from": "00:00:00,000", "to": "00:00:00,100"},
                "text": "hallo",
                "probability": 0.9,
            }
        ],
    }
    binary = _write_python_executable(
        tmp_path / "whisper-cli",
        "import json, sys, wave\n"
        "input_path = sys.argv[sys.argv.index('-f') + 1]\n"
        "with wave.open(input_path, 'rb') as source:\n"
        "    assert source.getnchannels() == 1\n"
        "    assert source.getframerate() == 16000\n"
        "output_prefix = sys.argv[sys.argv.index('-of') + 1]\n"
        f"payload = {payload!r}\n"
        "with open(f'{output_prefix}.json', 'w', encoding='utf-8') as handle:\n"
        "    json.dump(payload, handle)\n",
    )
    backend = WhisperCppBackend(binary=str(binary), model_path=str(model_path))

    result = backend.transcribe(filename="../../sample.wav", content=_wav_bytes())

    assert result.text == "hallo"
    assert result.segments[0].end_ms == 100
    assert result.confidence == 0.9


def test_whisper_cpp_rejects_oversized_json_output_file(monkeypatch, tmp_path):
    model_path = tmp_path / "base.bin"
    model_path.write_bytes(b"model")
    binary = _write_python_executable(
        tmp_path / "whisper-cli-oversized",
        "import sys\n"
        "output_prefix = sys.argv[sys.argv.index('-of') + 1]\n"
        "with open(f'{output_prefix}.json', 'wb') as handle:\n"
        "    handle.truncate(2049)\n",
    )
    monkeypatch.setattr(WhisperCppBackend, "_MAX_OUTPUT_BYTES", 2_048)
    backend = WhisperCppBackend(binary=str(binary), model_path=str(model_path))

    with pytest.raises(RuntimeError, match="output exceeds the configured limit"):
        backend.transcribe(filename="sample.wav", content=_wav_bytes())


def test_voxtral_uses_bounded_real_process_and_parses_json_stdout(tmp_path):
    model_path = tmp_path / "voxtral.gguf"
    model_path.write_bytes(b"model")
    runner = _write_python_executable(
        tmp_path / "voxtral-runner",
        "import json, sys, wave\n"
        "audio_path = sys.argv[sys.argv.index('--audio') + 1]\n"
        "with wave.open(audio_path, 'rb') as source:\n"
        "    assert source.getnchannels() == 1\n"
        "    assert source.getframerate() == 16000\n"
        "print(json.dumps({'transcript': 'voxtral hallo', 'language': 'de', 'confidence': 0.8}))\n",
    )
    backend = VoxtralBackend(
        model="voxtral",
        fallback_model="mock",
        model_path=str(model_path),
        runner_path=str(runner),
    )
    assert backend.list_models()[0]["status"] == "degraded"
    assert backend.list_models()[0]["reason_code"] == "voxtral.runtime_probe_pending"

    result = backend.transcribe(filename="../../sample.wav", content=_wav_bytes())

    assert result.text == "voxtral hallo"
    assert result.language == "de"
    assert result.confidence == 0.8
    assert backend.list_models()[0]["status"] == "ready"
    assert backend.list_models()[0]["reason_code"] is None


def test_voxtral_rejects_oversized_stdout(monkeypatch, tmp_path):
    model_path = tmp_path / "voxtral.gguf"
    model_path.write_bytes(b"model")
    runner = _write_python_executable(
        tmp_path / "voxtral-runner-oversized",
        "import os\nos.write(1, b'x' * 2049)\n",
    )
    monkeypatch.setattr(VoxtralBackend, "_MAX_OUTPUT_BYTES", 2_048)
    backend = VoxtralBackend(
        model="voxtral",
        fallback_model="mock",
        model_path=str(model_path),
        runner_path=str(runner),
    )

    with pytest.raises(RuntimeError, match="output exceeds the configured limit"):
        backend.transcribe(filename="sample.wav", content=_wav_bytes())


class _CancellingProcessRunner:
    def __init__(self, token: BackendCancellationToken) -> None:
        self._token = token

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
        del input_payload, max_stdout_bytes, timeout_seconds, cwd
        assert callable(cancellation_check)
        self._token.cancel()
        cancellation_check()
        raise AssertionError("cancellation callback must raise")


@pytest.mark.parametrize("backend_name", ["whisper_cpp", "voxtral"])
def test_local_subprocess_backends_propagate_active_cancellation(backend_name, tmp_path):
    token = BackendCancellationToken(deadline_monotonic=time.monotonic() + 5)
    process_runner = _CancellingProcessRunner(token)
    backend: WhisperCppBackend | VoxtralBackend
    if backend_name == "whisper_cpp":
        model_path = tmp_path / "base.bin"
        model_path.write_bytes(b"model")
        backend = WhisperCppBackend(
            binary="/bin/echo",
            model_path=str(model_path),
            process_runner=process_runner,
        )
    else:
        model_path = tmp_path / "voxtral.gguf"
        model_path.write_bytes(b"model")
        backend = VoxtralBackend(
            model="voxtral",
            fallback_model="mock",
            model_path=str(model_path),
            runner_path="/bin/echo",
            process_runner=process_runner,
        )

    with pytest.raises(BackendCancelledError):
        backend.transcribe_with_control(
            filename="sample.wav",
            content=_wav_bytes(),
            language="de",
            context={},
            cancellation_token=token,
            deadline_monotonic=token.deadline_monotonic,
        )


def test_whisper_cpp_incremental_recognizer_uses_bounded_real_growing_windows():
    backend = WhisperCppBackend(binary="/bin/echo", model_path="/models/base.bin")
    observed_frames: list[int] = []

    def transcribe_window(*, filename: str, content: bytes, language: str | None = None):
        with wave.open(io.BytesIO(content), "rb") as source:
            frames = source.getnframes()
            assert source.getframerate() == 16_000
        observed_frames.append(frames)
        return TranscriptionResult(
            text=f"partial-{frames}",
            language=language,
            duration_ms=frames * 1000 // 16_000,
            raw_backend="whisper_cpp",
        )

    backend.transcribe = transcribe_window  # type: ignore[method-assign]
    recognizer = backend.create_incremental_recognizer(
        filename="stream.pcm",
        language="de",
        max_bytes=6_400,
    )

    first = recognizer.accept(b"\x00\x00" * 1_600)
    second = recognizer.accept(b"\x00\x00" * 1_600)
    final = recognizer.finish()

    assert first == "partial-1600"
    assert second == "partial-3200"
    assert final.text == second
    assert observed_frames == [1_600, 3_200]
    recognizer.close()
    with pytest.raises(RuntimeError, match="closed"):
        recognizer.accept(b"\x00\x00")


class _FakeFasterModel:
    init_calls: list[tuple[object, dict[str, object]]] = []

    def __init__(self, model_path, **kwargs):
        self.init_calls.append((model_path, kwargs))

    def transcribe(self, input_path, **kwargs):
        with wave.open(input_path, "rb") as source:
            assert source.getframerate() == 16_000
        word = types.SimpleNamespace(probability=0.75)
        segment = types.SimpleNamespace(start=0.0, end=0.1, text=" test ", words=[word])
        return iter([segment]), types.SimpleNamespace(language="de")


def test_faster_whisper_is_local_only_and_maps_segments(tmp_path):
    _FakeFasterModel.init_calls.clear()
    model_path = tmp_path / "faster-model"
    model_path.mkdir()
    backend = FasterWhisperBackend(model_path=str(model_path), model_factory=_FakeFasterModel, device="cpu")

    result = backend.transcribe(filename="sample.wav", content=_wav_bytes())

    assert result.text == "test"
    assert result.language == "de"
    assert result.segments[0].confidence == 0.75
    assert _FakeFasterModel.init_calls == [
        (
            str(model_path.resolve()),
            {"device": "cpu", "compute_type": "default", "local_files_only": True},
        )
    ]


def test_faster_whisper_rejects_non_local_model_identifier():
    backend = FasterWhisperBackend(model_path="organization/model", model_factory=_FakeFasterModel)

    with pytest.raises(RuntimeError, match="does not exist"):
        backend.transcribe(filename="sample.wav", content=_wav_bytes())
