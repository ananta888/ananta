from __future__ import annotations

import io
import stat
import subprocess
import sys
import threading
import time
import wave
from pathlib import Path

import pytest

from voice_runtime.audio import AudioInputError, AudioPayloadLimits, normalize_audio_payload, sanitize_audio_filename
from voice_runtime.errors import BackendUnavailableError
from voice_runtime.preprocessing.audio_decode import (
    AudioDecodeError,
    AudioDecodeLimits,
    BoundedSubprocessRunner,
    FfmpegAudioDecoder,
    ProcessResult,
    WavPcmDecoder,
)
from voice_runtime.preprocessing.temp_workspace import temporary_audio_workspace


def _wav_bytes(*, duration_ms: int = 100, sample_rate: int = 8_000, channels: int = 1) -> bytes:
    frame_count = duration_ms * sample_rate // 1000
    frames = (b"\x00\x00" * channels) * frame_count
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(channels)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(frames)
    return buffer.getvalue()


def test_audio_filename_is_reduced_to_safe_basename():
    assert sanitize_audio_filename("../../secret/sample.wav") == "sample.wav"
    assert sanitize_audio_filename("..\\..\\evil.webm") == "evil.webm"
    assert sanitize_audio_filename("\x00") == "audio"


def test_strict_audio_validation_rejects_extension_magic_mismatch():
    with pytest.raises(AudioInputError) as exc_info:
        normalize_audio_payload(filename="renamed.mp3", payload=_wav_bytes(), strict=True)

    assert exc_info.value.code == "validation.audio_format_mismatch"


def test_strict_audio_validation_rejects_media_type_mismatch():
    with pytest.raises(AudioInputError) as exc_info:
        normalize_audio_payload(
            filename="sample.wav",
            payload=_wav_bytes(),
            media_type="audio/mpeg",
            strict=True,
        )

    assert exc_info.value.code == "validation.audio_media_type_mismatch"


def test_audio_validation_enforces_encoded_size_before_decode():
    with pytest.raises(AudioInputError) as exc_info:
        normalize_audio_payload(
            filename="sample.wav",
            payload=_wav_bytes(),
            limits=AudioPayloadLimits(max_encoded_bytes=10),
            strict=True,
        )

    assert exc_info.value.code == "validation.audio_too_large"


def test_wav_decoder_normalizes_stereo_and_sample_rate():
    decoded = WavPcmDecoder().decode(
        filename="../sample.wav",
        payload=_wav_bytes(duration_ms=125, sample_rate=8_000, channels=2),
    )

    assert decoded.filename == "sample.wav"
    assert decoded.channels == 1
    assert decoded.sample_width_bytes == 2
    assert decoded.sample_rate_hz == 16_000
    assert 123 <= decoded.duration_ms <= 125
    assert len(decoded.pcm_s16le) <= 4_000


def test_wav_decoder_rejects_declared_duration_limit():
    decoder = WavPcmDecoder(limits=AudioDecodeLimits(max_duration_ms=50))

    with pytest.raises(AudioDecodeError) as exc_info:
        decoder.decode(filename="sample.wav", payload=_wav_bytes(duration_ms=100))

    assert exc_info.value.code == "decode.duration_limit"


class _RecordingFfmpegRunner:
    def __init__(self, stdout: bytes) -> None:
        self.stdout = stdout
        self.calls: list[tuple[list[str], dict]] = []

    def run(self, argv: list[str], **kwargs) -> ProcessResult:
        self.calls.append((argv, kwargs))
        return ProcessResult(returncode=0, stdout=self.stdout)


def _write_python_executable(path: Path, body: str) -> Path:
    path.write_text(f"#!{sys.executable}\n{body}", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def _mp3_payload() -> bytes:
    return b"ID3\x04\x00\x00payload"


def _assert_no_process_io_threads() -> None:
    leaked = [thread.name for thread in threading.enumerate() if thread.name.startswith("voice-subprocess-")]
    assert leaked == []


def test_ffmpeg_decoder_uses_bounded_argv_only_execution(monkeypatch, tmp_path):
    binary = _write_python_executable(
        tmp_path / "ffmpeg",
        "import sys\nsys.stdin.buffer.read()\nsys.stdout.buffer.write(b'\\x00\\x00' * 1600)\n",
    )
    real_popen = subprocess.Popen
    calls: list[tuple[list[str], dict]] = []

    def recording_popen(argv, **kwargs):
        calls.append((argv, kwargs))
        return real_popen(argv, **kwargs)

    monkeypatch.setattr("voice_runtime.preprocessing.audio_decode.shutil.which", lambda _binary: str(binary))
    monkeypatch.setattr("voice_runtime.preprocessing.audio_decode.subprocess.Popen", recording_popen)
    decoder = FfmpegAudioDecoder()

    decoded = decoder.decode(filename="sample.mp3", payload=_mp3_payload())

    argv, kwargs = calls[0]
    assert isinstance(argv, list)
    assert kwargs["shell"] is False
    assert kwargs["start_new_session"] is True
    assert kwargs["close_fds"] is True
    assert kwargs["env"] == {"LANG": "C", "LC_ALL": "C", "TZ": "UTC"}
    assert kwargs["stdin"] is subprocess.PIPE
    assert kwargs["stdout"] is subprocess.PIPE
    assert kwargs["stderr"] is subprocess.PIPE
    assert argv[argv.index("-protocol_whitelist") + 1] == "pipe"
    blacklist = argv[argv.index("-protocol_blacklist") + 1]
    assert "http" in blacklist.split(",")
    assert "file" in blacklist.split(",")
    assert argv[argv.index("-fs") + 1] == "19200000"
    assert [argv[argv.index("-map") + 1], *[item for item in ("-vn", "-sn", "-dn") if item in argv]] == [
        "0:a:0",
        "-vn",
        "-sn",
        "-dn",
    ]
    assert "sample.mp3" not in argv
    assert not Path(kwargs["cwd"]).exists()
    assert decoded.duration_ms == 100


def test_ffmpeg_runner_stops_and_rejects_output_above_pcm_budget(monkeypatch, tmp_path):
    binary = _write_python_executable(
        tmp_path / "ffmpeg-overflow",
        "import os, sys\nsys.stdin.buffer.read()\n"
        "chunk = b'x' * 65536\n"
        "while True:\n    os.write(1, chunk)\n",
    )
    monkeypatch.setattr("voice_runtime.preprocessing.audio_decode.shutil.which", lambda _binary: str(binary))
    decoder = FfmpegAudioDecoder(
        binary=str(binary),
        limits=AudioDecodeLimits(max_decoded_pcm_bytes=1_024, ffmpeg_timeout_sec=3),
    )

    with pytest.raises(AudioDecodeError) as exc_info:
        decoder.decode(filename="sample.mp3", payload=_mp3_payload())

    assert exc_info.value.code == "decode.pcm_size_limit"
    _assert_no_process_io_threads()


def test_ffmpeg_runner_bounds_stderr_without_exposing_it(tmp_path):
    binary = _write_python_executable(
        tmp_path / "ffmpeg-stderr",
        "import os, sys\nsys.stdin.buffer.read()\n"
        "os.write(2, b'sensitive-error' * 10000)\nos.write(1, b'\\x00\\x00')\n",
    )

    result = BoundedSubprocessRunner().run(
        [str(binary)],
        input_payload=b"audio",
        max_stdout_bytes=1_024,
        timeout_seconds=3,
        cwd=tmp_path,
    )

    assert result.returncode == 0
    assert result.stdout == b"\x00\x00"
    assert result.stderr_truncated is True


def test_ffmpeg_timeout_kills_the_complete_process_group(monkeypatch, tmp_path):
    child_marker = tmp_path / "timeout-child-survived"
    ready_marker = tmp_path / "timeout-child-started"
    child_code = (
        "import pathlib,time; time.sleep(1.5); "
        f"pathlib.Path({str(child_marker)!r}).write_text('survived')"
    )
    binary = _write_python_executable(
        tmp_path / "ffmpeg-timeout",
        "import pathlib, subprocess, sys, time\n"
        f"subprocess.Popen([sys.executable, '-c', {child_code!r}])\n"
        f"pathlib.Path({str(ready_marker)!r}).write_text('started')\n"
        "sys.stdin.buffer.read()\ntime.sleep(30)\n",
    )
    monkeypatch.setattr("voice_runtime.preprocessing.audio_decode.shutil.which", lambda _binary: str(binary))
    decoder = FfmpegAudioDecoder(binary=str(binary), limits=AudioDecodeLimits(ffmpeg_timeout_sec=1))

    with pytest.raises(TimeoutError, match="ffmpeg audio decode timed out"):
        decoder.decode(filename="sample.mp3", payload=_mp3_payload())

    assert ready_marker.exists()
    time.sleep(1.7)
    assert not child_marker.exists()
    _assert_no_process_io_threads()


def test_ffmpeg_error_kills_descendants_after_direct_process_exit(monkeypatch, tmp_path):
    child_marker = tmp_path / "error-child-survived"
    ready_marker = tmp_path / "error-child-started"
    child_code = (
        "import pathlib,time; time.sleep(1.5); "
        f"pathlib.Path({str(child_marker)!r}).write_text('survived')"
    )
    binary = _write_python_executable(
        tmp_path / "ffmpeg-error",
        "import pathlib, subprocess, sys\n"
        f"subprocess.Popen([sys.executable, '-c', {child_code!r}])\n"
        f"pathlib.Path({str(ready_marker)!r}).write_text('started')\n"
        "sys.stdin.buffer.read()\nraise SystemExit(7)\n",
    )
    monkeypatch.setattr("voice_runtime.preprocessing.audio_decode.shutil.which", lambda _binary: str(binary))
    decoder = FfmpegAudioDecoder(binary=str(binary))

    with pytest.raises(AudioDecodeError) as exc_info:
        decoder.decode(filename="sample.mp3", payload=_mp3_payload())

    assert exc_info.value.code == "decode.ffmpeg_failed"
    assert ready_marker.exists()
    time.sleep(1.7)
    assert not child_marker.exists()
    _assert_no_process_io_threads()


class _InjectedCancellation(BaseException):
    pass


def test_bounded_runner_cancellation_check_kills_process_group(tmp_path):
    child_marker = tmp_path / "cancel-child-survived"
    ready_marker = tmp_path / "cancel-child-started"
    child_code = (
        "import pathlib,time; time.sleep(1.5); "
        f"pathlib.Path({str(child_marker)!r}).write_text('survived')"
    )
    binary = _write_python_executable(
        tmp_path / "ffmpeg-cancel",
        "import pathlib, subprocess, sys, time\n"
        f"subprocess.Popen([sys.executable, '-c', {child_code!r}])\n"
        f"pathlib.Path({str(ready_marker)!r}).write_text('started')\n"
        "sys.stdin.buffer.read()\ntime.sleep(30)\n",
    )
    def cancellation_check() -> None:
        if ready_marker.exists():
            raise _InjectedCancellation()

    with pytest.raises(_InjectedCancellation):
        BoundedSubprocessRunner().run(
            [str(binary)],
            input_payload=b"audio",
            max_stdout_bytes=1_024,
            timeout_seconds=5,
            cwd=tmp_path,
            cancellation_check=cancellation_check,
        )

    assert ready_marker.exists()
    time.sleep(1.7)
    assert not child_marker.exists()
    _assert_no_process_io_threads()


def test_ffmpeg_decoder_injected_runner_receives_bounded_input_and_ephemeral_workspace(monkeypatch, tmp_path):
    binary = _write_python_executable(tmp_path / "ffmpeg-injected", "raise SystemExit(99)\n")
    runner = _RecordingFfmpegRunner(stdout=b"\x00\x00" * 1_600)
    monkeypatch.setattr("voice_runtime.preprocessing.audio_decode.shutil.which", lambda _binary: str(binary))
    decoder = FfmpegAudioDecoder(binary=str(binary), process_runner=runner)

    decoded = decoder.decode(filename="sample.mp3", payload=_mp3_payload())

    argv, kwargs = runner.calls[0]
    assert argv[0] == str(binary.resolve())
    assert kwargs["input_payload"] == _mp3_payload()
    assert kwargs["max_stdout_bytes"] == 19_200_000
    assert kwargs["timeout_seconds"] == 30
    assert not kwargs["cwd"].exists()
    assert decoded.duration_ms == 100


def test_ffmpeg_decoder_reports_missing_dependency_as_unavailable(monkeypatch):
    monkeypatch.setattr("voice_runtime.preprocessing.audio_decode.shutil.which", lambda _binary: None)

    with pytest.raises(BackendUnavailableError) as exc_info:
        FfmpegAudioDecoder().decode(filename="sample.mp3", payload=_mp3_payload())

    assert exc_info.value.code == "unavailable"
    assert "ffmpeg" in exc_info.value.message


def test_ffmpeg_decoder_rejects_incomplete_pcm_sample(monkeypatch, tmp_path):
    binary = _write_python_executable(tmp_path / "ffmpeg-odd-pcm", "raise SystemExit(99)\n")
    runner = _RecordingFfmpegRunner(stdout=b"\x00")
    monkeypatch.setattr("voice_runtime.preprocessing.audio_decode.shutil.which", lambda _binary: str(binary))

    with pytest.raises(AudioDecodeError) as exc_info:
        FfmpegAudioDecoder(binary=str(binary), process_runner=runner).decode(
            filename="sample.mp3",
            payload=_mp3_payload(),
        )

    assert exc_info.value.code == "decode.invalid_pcm"


class _ExceptionalFfmpegRunner:
    def __init__(self, outcome: BaseException | ProcessResult) -> None:
        self.outcome = outcome
        self.cwd: Path | None = None

    def run(
        self,
        _argv: list[str],
        *,
        input_payload: bytes,
        max_stdout_bytes: int,
        timeout_seconds: float,
        cwd: Path,
        cancellation_check=None,
    ) -> ProcessResult:
        del input_payload, max_stdout_bytes, timeout_seconds, cancellation_check
        self.cwd = cwd
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


@pytest.mark.parametrize(
    "outcome,expected_exception",
    [
        (ProcessResult(returncode=9, stdout=b""), AudioDecodeError),
        (TimeoutError("timeout"), TimeoutError),
        (_InjectedCancellation(), _InjectedCancellation),
    ],
    ids=("decoder-error", "timeout", "cancellation"),
)
def test_ffmpeg_workspace_is_removed_on_every_failure_path(
    monkeypatch,
    tmp_path,
    outcome,
    expected_exception,
):
    binary = _write_python_executable(tmp_path / f"ffmpeg-{expected_exception.__name__}", "raise SystemExit(99)\n")
    runner = _ExceptionalFfmpegRunner(outcome)
    monkeypatch.setattr("voice_runtime.preprocessing.audio_decode.shutil.which", lambda _binary: str(binary))

    with pytest.raises(expected_exception):
        FfmpegAudioDecoder(binary=str(binary), process_runner=runner).decode(
            filename="sample.mp3",
            payload=_mp3_payload(),
        )

    assert runner.cwd is not None
    assert not runner.cwd.exists()


def test_audio_workspace_is_private_and_removed_after_exception():
    root = None
    with pytest.raises(RuntimeError):
        with temporary_audio_workspace() as workspace:
            root = workspace.root
            payload_path = workspace.write_bytes("../../sample.wav", b"audio", max_bytes=10)
            assert stat.S_IMODE(workspace.root.stat().st_mode) == 0o700
            assert stat.S_IMODE(payload_path.stat().st_mode) == 0o600
            raise RuntimeError("expected")

    assert root is not None
    assert not root.exists()
