from __future__ import annotations

import errno
import io
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import wave
from array import array
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Callable, Protocol

from ..audio import AudioInputError, AudioPayloadLimits, normalize_audio_payload
from ..errors import BackendUnavailableError
from .temp_workspace import temporary_audio_workspace


class AudioDecodeError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class AudioDecodeLimits:
    max_encoded_bytes: int = 25 * 1024 * 1024
    max_decoded_pcm_bytes: int = 64 * 1024 * 1024
    max_duration_ms: int = 10 * 60 * 1000
    max_channels: int = 2
    max_sample_rate_hz: int = 96_000
    target_sample_rate_hz: int = 16_000
    ffmpeg_timeout_sec: int = 30

    def __post_init__(self) -> None:
        numeric_values = (
            self.max_encoded_bytes,
            self.max_decoded_pcm_bytes,
            self.max_duration_ms,
            self.max_channels,
            self.max_sample_rate_hz,
            self.target_sample_rate_hz,
            self.ffmpeg_timeout_sec,
        )
        if any(value <= 0 for value in numeric_values):
            raise ValueError("audio decode limits must be positive")
        if self.target_sample_rate_hz > self.max_sample_rate_hz:
            raise ValueError("target sample rate cannot exceed max sample rate")


@dataclass(frozen=True)
class DecodedPcmAudio:
    filename: str
    pcm_s16le: bytes
    sample_rate_hz: int
    duration_ms: int
    source_format: str
    timeline_start_ms: int = 0
    channels: int = 1
    sample_width_bytes: int = 2

    @property
    def frame_count(self) -> int:
        frame_width = self.channels * self.sample_width_bytes
        return len(self.pcm_s16le) // frame_width if frame_width else 0

    @property
    def timeline_end_ms(self) -> int:
        return self.timeline_start_ms + self.duration_ms

    def slice_ms(self, start_ms: int, end_ms: int) -> "DecodedPcmAudio":
        bounded_start = max(0, min(int(start_ms), self.duration_ms))
        bounded_end = max(bounded_start, min(int(end_ms), self.duration_ms))
        start_frame = bounded_start * self.sample_rate_hz // 1000
        end_frame = bounded_end * self.sample_rate_hz // 1000
        frame_width = self.channels * self.sample_width_bytes
        sliced = self.pcm_s16le[start_frame * frame_width : end_frame * frame_width]
        actual_duration = len(sliced) * 1000 // max(1, self.sample_rate_hz * frame_width)
        return DecodedPcmAudio(
            filename=self.filename,
            pcm_s16le=sliced,
            sample_rate_hz=self.sample_rate_hz,
            duration_ms=actual_duration,
            source_format=self.source_format,
            timeline_start_ms=self.timeline_start_ms + bounded_start,
            channels=self.channels,
            sample_width_bytes=self.sample_width_bytes,
        )

    def to_wav_bytes(self) -> bytes:
        output = io.BytesIO()
        with wave.open(output, "wb") as destination:
            destination.setnchannels(self.channels)
            destination.setsampwidth(self.sample_width_bytes)
            destination.setframerate(self.sample_rate_hz)
            destination.writeframes(self.pcm_s16le)
        return output.getvalue()


class AudioDecoder(Protocol):
    def decode(self, *, filename: str, payload: bytes) -> DecodedPcmAudio: ...


@dataclass(frozen=True)
class ProcessResult:
    returncode: int
    stdout: bytes
    stderr_truncated: bool = False


class ProcessRunner(Protocol):
    def run(
        self,
        argv: list[str],
        *,
        input_payload: bytes,
        max_stdout_bytes: int,
        timeout_seconds: float,
        cwd: Path,
        cancellation_check: Callable[[], None] | None = None,
    ) -> ProcessResult: ...


class FfmpegProcessRunner(Protocol):
    """Compatibility port for existing ffmpeg decoder runner injections."""

    def run(
        self,
        argv: list[str],
        *,
        input_payload: bytes,
        max_stdout_bytes: int,
        timeout_seconds: float,
        cwd: Path,
    ) -> ProcessResult: ...


class ProcessOutputLimitError(RuntimeError):
    """Raised when a child exceeds its configured stdout byte budget."""


class ProcessPipeError(RuntimeError):
    """Raised when a successful child cannot be drained safely."""


class BoundedSubprocessRunner:
    """Execute a trusted local binary with bounded pipe collectors.

    The process starts in its own process group. Every exit path kills any
    remaining descendants and closes all inherited pipes. Output collectors
    retain at most their configured byte budget, avoiding ``communicate`` and
    ``subprocess.run``'s unbounded in-memory stdout buffering.
    """

    _READ_CHUNK_BYTES = 64 * 1024
    _MAX_STDERR_BYTES = 64 * 1024
    _TERMINATION_GRACE_SECONDS = 0.2
    _THREAD_JOIN_SECONDS = 1.0
    _WAIT_POLL_SECONDS = 0.05

    def __init__(self, *, library_paths: tuple[str, ...] = ()) -> None:
        # Operator-owned GPU library directories only. Do not inherit service
        # credentials or accept arbitrary environment variables from audio jobs.
        if len(library_paths) > 16 or any(
            not isinstance(path, str)
            or not Path(path).is_absolute()
            or len(path) > 512
            or ":" in path
            or "\x00" in path
            for path in library_paths
        ):
            raise ValueError("bounded_process_library_paths_invalid")
        self._environment = {"LANG": "C", "LC_ALL": "C", "TZ": "UTC"}
        if library_paths:
            self._environment["LD_LIBRARY_PATH"] = ":".join(library_paths)

    def run(
        self,
        argv: list[str],
        *,
        input_payload: bytes,
        max_stdout_bytes: int,
        timeout_seconds: float,
        cwd: Path,
        cancellation_check: Callable[[], None] | None = None,
    ) -> ProcessResult:
        if max_stdout_bytes <= 0 or timeout_seconds <= 0:
            raise ValueError("bounded process limits must be positive")
        process = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(cwd),
            env=self._environment,
            shell=False,
            close_fds=True,
            bufsize=0,
            start_new_session=True,
        )
        if process.stdin is None or process.stdout is None or process.stderr is None:
            self._terminate_process_group(process)
            for stream in (process.stdin, process.stdout, process.stderr):
                self._close_pipe(stream)
            raise RuntimeError("bounded process pipes are unavailable")

        stdout_buffer = bytearray()
        stderr_buffer = bytearray()
        stdout_overflow = threading.Event()
        stderr_truncated = threading.Event()
        io_errors: list[BaseException] = []
        io_errors_lock = threading.Lock()

        stdout_thread = threading.Thread(
            target=self._drain_pipe,
            kwargs={
                "stream": process.stdout,
                "destination": stdout_buffer,
                "max_bytes": max_stdout_bytes,
                "overflow": stdout_overflow,
                "process": process,
                "terminate_on_overflow": True,
                "io_errors": io_errors,
                "io_errors_lock": io_errors_lock,
            },
            name="voice-subprocess-stdout",
            daemon=True,
        )
        stderr_thread = threading.Thread(
            target=self._drain_pipe,
            kwargs={
                "stream": process.stderr,
                "destination": stderr_buffer,
                "max_bytes": self._MAX_STDERR_BYTES,
                "overflow": stderr_truncated,
                "process": process,
                "terminate_on_overflow": False,
                "io_errors": io_errors,
                "io_errors_lock": io_errors_lock,
            },
            name="voice-subprocess-stderr",
            daemon=True,
        )
        stdin_thread = threading.Thread(
            target=self._write_stdin,
            kwargs={
                "stream": process.stdin,
                "payload": input_payload,
                "io_errors": io_errors,
                "io_errors_lock": io_errors_lock,
            },
            name="voice-subprocess-stdin",
            daemon=True,
        )
        threads = (stdout_thread, stderr_thread, stdin_thread)
        started_threads: list[threading.Thread] = []
        returncode: int | None = None
        try:
            for thread in threads:
                thread.start()
                started_threads.append(thread)
            returncode = self._wait_for_process(
                process,
                timeout_seconds=timeout_seconds,
                cancellation_check=cancellation_check,
            )
        except BaseException:
            self._terminate_process_group(process)
            raise
        finally:
            if process.poll() is None:
                self._terminate_process_group(process)
            else:
                self._kill_remaining_process_group(process)
            self._join_and_close_pipes(process, tuple(started_threads))

        if stdout_overflow.is_set():
            raise ProcessOutputLimitError("subprocess stdout exceeds the configured byte limit")
        if io_errors and returncode == 0:
            raise ProcessPipeError("subprocess pipe processing failed")
        return ProcessResult(
            returncode=int(returncode),
            stdout=bytes(stdout_buffer),
            stderr_truncated=stderr_truncated.is_set(),
        )

    def _wait_for_process(
        self,
        process: subprocess.Popen[bytes],
        *,
        timeout_seconds: float,
        cancellation_check: Callable[[], None] | None,
    ) -> int:
        deadline = time.monotonic() + timeout_seconds
        while True:
            if cancellation_check is not None:
                cancellation_check()
            returncode = process.poll()
            if returncode is not None:
                return int(returncode)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("bounded subprocess timed out")
            try:
                return process.wait(timeout=min(self._WAIT_POLL_SECONDS, remaining))
            except subprocess.TimeoutExpired:
                continue

    def _drain_pipe(
        self,
        *,
        stream: IO[bytes],
        destination: bytearray,
        max_bytes: int,
        overflow: threading.Event,
        process: subprocess.Popen[bytes],
        terminate_on_overflow: bool,
        io_errors: list[BaseException],
        io_errors_lock: threading.Lock,
    ) -> None:
        try:
            while True:
                chunk = stream.read(self._READ_CHUNK_BYTES)
                if not chunk:
                    return
                remaining = max(0, max_bytes - len(destination))
                if remaining:
                    destination.extend(chunk[:remaining])
                if len(chunk) > remaining:
                    overflow.set()
                    if terminate_on_overflow:
                        self._signal_process_group(process, signal.SIGKILL)
                        return
        except (BrokenPipeError, ValueError):
            return
        except OSError as exc:
            if exc.errno not in {errno.EBADF, errno.EPIPE}:
                with io_errors_lock:
                    io_errors.append(exc)
        except BaseException as exc:
            with io_errors_lock:
                io_errors.append(exc)
        finally:
            self._close_pipe(stream)

    def _write_stdin(
        self,
        *,
        stream: IO[bytes],
        payload: bytes,
        io_errors: list[BaseException],
        io_errors_lock: threading.Lock,
    ) -> None:
        try:
            view = memoryview(payload)
            offset = 0
            while offset < len(view):
                written = stream.write(view[offset : offset + self._READ_CHUNK_BYTES])
                if not written:
                    raise OSError(errno.EPIPE, "subprocess stdin closed")
                offset += written
            stream.flush()
        except (BrokenPipeError, ValueError):
            return
        except OSError as exc:
            if exc.errno not in {errno.EBADF, errno.EPIPE}:
                with io_errors_lock:
                    io_errors.append(exc)
        except BaseException as exc:
            with io_errors_lock:
                io_errors.append(exc)
        finally:
            self._close_pipe(stream)

    def _terminate_process_group(self, process: subprocess.Popen[bytes]) -> None:
        self._signal_process_group(process, signal.SIGTERM)
        try:
            process.wait(timeout=self._TERMINATION_GRACE_SECONDS)
        except BaseException:
            pass
        self._signal_process_group(process, signal.SIGKILL)
        try:
            process.wait(timeout=self._TERMINATION_GRACE_SECONDS)
        except BaseException:
            try:
                process.kill()
            except BaseException:
                pass

    def _kill_remaining_process_group(self, process: subprocess.Popen[bytes]) -> None:
        # A malformed decoder input must not leave a helper child alive after
        # the direct ffmpeg process has already exited.
        self._signal_process_group(process, signal.SIGKILL)

    @staticmethod
    def _signal_process_group(process: subprocess.Popen[bytes], requested_signal: signal.Signals) -> None:
        try:
            if os.name == "posix":
                os.killpg(process.pid, requested_signal)
            elif requested_signal == signal.SIGTERM:
                process.terminate()
            else:
                process.kill()
        except (OSError, ProcessLookupError):
            pass

    def _join_and_close_pipes(
        self,
        process: subprocess.Popen[bytes],
        threads: tuple[threading.Thread, ...],
    ) -> None:
        for thread in threads:
            thread.join(timeout=self._THREAD_JOIN_SECONDS)
        for stream in (process.stdin, process.stdout, process.stderr):
            self._close_pipe(stream)
        for thread in threads:
            if thread.is_alive():
                thread.join(timeout=self._THREAD_JOIN_SECONDS)

    @staticmethod
    def _close_pipe(stream: IO[bytes] | None) -> None:
        if stream is None:
            return
        try:
            stream.close()
        except (OSError, ValueError):
            pass


# Compatibility names keep the existing audio-decoder port stable while the
# same generic runner is reused by local transcription backends.
FfmpegProcessResult = ProcessResult
BoundedFfmpegProcessRunner = BoundedSubprocessRunner


class WavPcmDecoder:
    def __init__(self, *, limits: AudioDecodeLimits | None = None) -> None:
        self._limits = limits or AudioDecodeLimits()

    def decode(self, *, filename: str, payload: bytes) -> DecodedPcmAudio:
        normalized = _normalize_for_decode(filename=filename, payload=payload, limits=self._limits)
        if normalized.detected_format != "wav":
            raise AudioDecodeError("decode.not_wav", "stdlib decoder accepts PCM WAV only")
        try:
            with wave.open(io.BytesIO(normalized.payload), "rb") as source:
                channels = source.getnchannels()
                sample_width = source.getsampwidth()
                sample_rate = source.getframerate()
                frame_count = source.getnframes()
                compression = source.getcomptype()
                self._validate_header(
                    channels=channels,
                    sample_width=sample_width,
                    sample_rate=sample_rate,
                    frame_count=frame_count,
                    compression=compression,
                )
                frames = source.readframes(frame_count)
        except (EOFError, wave.Error) as exc:
            raise AudioDecodeError("decode.invalid_wav", "invalid or truncated WAV payload") from exc

        expected_bytes = frame_count * channels * sample_width
        if len(frames) != expected_bytes:
            raise AudioDecodeError("decode.truncated_wav", "WAV data is shorter than its declared frame count")
        if not frames:
            raise AudioDecodeError("decode.empty_pcm", "WAV payload contains no audio frames")
        pcm = self._normalize_pcm(
            frames=frames,
            channels=channels,
            sample_width=sample_width,
            sample_rate=sample_rate,
        )
        self._validate_decoded_size(pcm)
        duration_ms = len(pcm) * 1000 // (self._limits.target_sample_rate_hz * 2)
        return DecodedPcmAudio(
            filename=normalized.filename,
            pcm_s16le=pcm,
            sample_rate_hz=self._limits.target_sample_rate_hz,
            duration_ms=duration_ms,
            source_format="wav",
        )

    def _validate_header(
        self,
        *,
        channels: int,
        sample_width: int,
        sample_rate: int,
        frame_count: int,
        compression: str,
    ) -> None:
        if compression != "NONE":
            raise AudioDecodeError("decode.compressed_wav", "compressed WAV is not supported by the stdlib decoder")
        if channels < 1 or channels > self._limits.max_channels:
            raise AudioDecodeError("decode.channel_limit", "audio channel count exceeds the configured limit")
        if sample_width not in {1, 2, 3, 4}:
            raise AudioDecodeError("decode.sample_width", "WAV sample width is unsupported")
        if sample_rate < 1 or sample_rate > self._limits.max_sample_rate_hz:
            raise AudioDecodeError("decode.sample_rate_limit", "audio sample rate exceeds the configured limit")
        duration_ms = frame_count * 1000 // sample_rate
        if duration_ms > self._limits.max_duration_ms:
            raise AudioDecodeError("decode.duration_limit", "decoded audio exceeds the configured duration limit")
        if frame_count * channels * sample_width > self._limits.max_decoded_pcm_bytes:
            raise AudioDecodeError("decode.pcm_size_limit", "declared PCM payload exceeds the configured byte limit")

    def _normalize_pcm(self, *, frames: bytes, channels: int, sample_width: int, sample_rate: int) -> bytes:
        samples = _decode_little_endian_samples(frames, sample_width=sample_width)
        if channels == 2:
            samples = array("h", ((samples[index] + samples[index + 1]) // 2 for index in range(0, len(samples), 2)))
        if sample_rate != self._limits.target_sample_rate_hz:
            samples = _resample_s16(samples, source_rate=sample_rate, target_rate=self._limits.target_sample_rate_hz)
        if sys.byteorder != "little":
            samples.byteswap()
        return samples.tobytes()

    def _validate_decoded_size(self, pcm: bytes) -> None:
        if len(pcm) > self._limits.max_decoded_pcm_bytes:
            raise AudioDecodeError("decode.pcm_size_limit", "normalized PCM exceeds the configured byte limit")
        duration_ms = len(pcm) * 1000 // (self._limits.target_sample_rate_hz * 2)
        if duration_ms > self._limits.max_duration_ms:
            raise AudioDecodeError("decode.duration_limit", "normalized PCM exceeds the configured duration limit")


class FfmpegAudioDecoder:
    _DEMUXERS = {"mp3": "mp3", "ogg": "ogg", "webm": "matroska,webm", "m4a": "mov,mp4,m4a,3gp,3g2,mj2"}

    def __init__(
        self,
        *,
        binary: str = "ffmpeg",
        limits: AudioDecodeLimits | None = None,
        process_runner: FfmpegProcessRunner | None = None,
    ) -> None:
        self._binary = binary
        self._limits = limits or AudioDecodeLimits()
        self._process_runner = process_runner or BoundedSubprocessRunner()

    def build_argv(self, *, detected_format: str, binary: str | None = None) -> list[str]:
        demuxer = self._DEMUXERS.get(detected_format)
        if not demuxer:
            raise AudioDecodeError("decode.ffmpeg_format", "container is not allowed for ffmpeg decode")
        duration_seconds = self._limits.max_duration_ms / 1000
        return [
            binary or self._binary,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-protocol_whitelist",
            "pipe",
            "-protocol_blacklist",
            "async,cache,concat,concatf,crypto,data,file,ftp,gopher,hls,http,https,icecast,mmsh,mmst,rtmp,rtmps,"
            "rtmpt,rtmpts,rtp,sctp,srt,srtp,subfile,tcp,tls,udp,unix",
            "-probesize",
            "1048576",
            "-analyzeduration",
            "5000000",
            "-max_alloc",
            str(max(1_048_576, self._max_pcm_output_bytes())),
            "-f",
            demuxer,
            "-i",
            "pipe:0",
            "-t",
            f"{duration_seconds:.3f}",
            "-map_metadata",
            "-1",
            "-map",
            "0:a:0",
            "-vn",
            "-sn",
            "-dn",
            "-threads",
            "1",
            "-ac",
            "1",
            "-ar",
            str(self._limits.target_sample_rate_hz),
            "-c:a",
            "pcm_s16le",
            "-fs",
            str(self._max_pcm_output_bytes()),
            "-f",
            "s16le",
            "pipe:1",
        ]

    def decode(self, *, filename: str, payload: bytes) -> DecodedPcmAudio:
        normalized = _normalize_for_decode(filename=filename, payload=payload, limits=self._limits)
        detected_format = normalized.detected_format or ""
        binary = self._resolve_binary()
        argv = self.build_argv(detected_format=detected_format, binary=binary)
        with temporary_audio_workspace(prefix="ananta-voice-decode-") as workspace:
            try:
                completed = self._process_runner.run(
                    argv,
                    input_payload=normalized.payload,
                    max_stdout_bytes=self._max_pcm_output_bytes(),
                    timeout_seconds=self._limits.ffmpeg_timeout_sec,
                    cwd=workspace.root,
                )
            except ProcessOutputLimitError as exc:
                raise AudioDecodeError(
                    "decode.pcm_size_limit",
                    "ffmpeg PCM output exceeds the configured byte limit",
                ) from exc
            except ProcessPipeError as exc:
                raise AudioDecodeError("decode.ffmpeg_io", "ffmpeg pipe processing failed") from exc
            except TimeoutError as exc:
                raise TimeoutError("ffmpeg audio decode timed out") from exc
        if completed.returncode != 0:
            raise AudioDecodeError(
                "decode.ffmpeg_failed",
                f"ffmpeg audio decode failed with exit code {completed.returncode}",
            )
        pcm = bytes(completed.stdout)
        if not pcm:
            raise AudioDecodeError("decode.empty_pcm", "ffmpeg produced no PCM audio")
        if len(pcm) % 2:
            raise AudioDecodeError("decode.invalid_pcm", "ffmpeg produced an incomplete PCM sample")
        if len(pcm) > self._limits.max_decoded_pcm_bytes:
            raise AudioDecodeError("decode.pcm_size_limit", "ffmpeg PCM output exceeds the configured byte limit")
        duration_ms = len(pcm) * 1000 // (self._limits.target_sample_rate_hz * 2)
        if duration_ms > self._limits.max_duration_ms:
            raise AudioDecodeError("decode.duration_limit", "ffmpeg PCM output exceeds the configured duration limit")
        return DecodedPcmAudio(
            filename=normalized.filename,
            pcm_s16le=pcm,
            sample_rate_hz=self._limits.target_sample_rate_hz,
            duration_ms=duration_ms,
            source_format=detected_format,
        )

    def _max_pcm_output_bytes(self) -> int:
        duration_bytes = self._limits.target_sample_rate_hz * 2 * self._limits.max_duration_ms // 1000
        return max(2, min(self._limits.max_decoded_pcm_bytes, duration_bytes))

    def _resolve_binary(self) -> str:
        resolved = shutil.which(self._binary)
        if not resolved:
            raise BackendUnavailableError("ffmpeg audio decoder is unavailable")
        try:
            path = Path(resolved).resolve(strict=True)
        except OSError as exc:
            raise BackendUnavailableError("ffmpeg audio decoder is unavailable") from exc
        if not path.is_file() or not os.access(path, os.X_OK):
            raise BackendUnavailableError("ffmpeg audio decoder is unavailable")
        return str(path)


class SafeAudioDecoder:
    def __init__(
        self,
        *,
        limits: AudioDecodeLimits | None = None,
        wav_decoder: AudioDecoder | None = None,
        ffmpeg_decoder: AudioDecoder | None = None,
    ) -> None:
        self._limits = limits or AudioDecodeLimits()
        self._wav_decoder = wav_decoder or WavPcmDecoder(limits=self._limits)
        self._ffmpeg_decoder = ffmpeg_decoder or FfmpegAudioDecoder(limits=self._limits)

    def decode(self, *, filename: str, payload: bytes) -> DecodedPcmAudio:
        normalized = _normalize_for_decode(filename=filename, payload=payload, limits=self._limits)
        if normalized.detected_format == "wav":
            return self._wav_decoder.decode(filename=normalized.filename, payload=normalized.payload)
        return self._ffmpeg_decoder.decode(filename=normalized.filename, payload=normalized.payload)


def _normalize_for_decode(*, filename: str, payload: bytes, limits: AudioDecodeLimits):
    try:
        return normalize_audio_payload(
            filename=filename,
            payload=payload,
            limits=AudioPayloadLimits(max_encoded_bytes=limits.max_encoded_bytes),
            strict=True,
        )
    except AudioInputError as exc:
        raise AudioDecodeError(exc.code, str(exc)) from exc


def _decode_little_endian_samples(frames: bytes, *, sample_width: int) -> array:
    if sample_width == 2:
        samples = array("h")
        samples.frombytes(frames)
        if sys.byteorder != "little":
            samples.byteswap()
        return samples
    if sample_width == 1:
        return array("h", ((value - 128) << 8 for value in frames))

    shift = 8 * (sample_width - 2)
    samples = array("h")
    for offset in range(0, len(frames), sample_width):
        raw = int.from_bytes(frames[offset : offset + sample_width], byteorder="little", signed=True)
        samples.append(max(-32_768, min(32_767, raw >> shift)))
    return samples


def _resample_s16(samples: array, *, source_rate: int, target_rate: int) -> array:
    if not samples or source_rate == target_rate:
        return samples
    output_count = max(1, len(samples) * target_rate // source_rate)
    output = array("h")
    for output_index in range(output_count):
        position = output_index * source_rate
        left_index = min(len(samples) - 1, position // target_rate)
        fraction = position % target_rate
        right_index = min(len(samples) - 1, left_index + 1)
        interpolated = (samples[left_index] * (target_rate - fraction) + samples[right_index] * fraction) // target_rate
        output.append(max(-32_768, min(32_767, interpolated)))
    return output
