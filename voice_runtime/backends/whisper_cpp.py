from __future__ import annotations

import io
import json
import math
import os
import shutil
import wave
from pathlib import Path
from typing import Any

from ..execution_control import BackendCancellationToken
from ..preprocessing.audio_decode import (
    AudioDecodeLimits,
    AudioDecoder,
    BoundedSubprocessRunner,
    ProcessOutputLimitError,
    ProcessPipeError,
    ProcessRunner,
    SafeAudioDecoder,
)
from ..preprocessing.temp_workspace import temporary_audio_workspace
from .base import ChatResult, TranscriptionResult, TranscriptionSegment, VoiceBackend


class WhisperCppBackend(VoiceBackend):
    """Local whisper.cpp adapter with allowlisted argv-only execution."""

    _MAX_OUTPUT_BYTES = 8 * 1024 * 1024
    _VALUE_FLAGS = {
        "--best-of": (1, 20, int),
        "--beam-size": (1, 20, int),
        "-bs": (1, 20, int),
        "--threads": (1, 256, int),
        "-t": (1, 256, int),
        "--gpu-layers": (0, 512, int),
        "-ngl": (0, 512, int),
        "--temperature": (0.0, 2.0, float),
    }
    _BOOLEAN_FLAGS = {"--no-fallback", "--split-on-word", "--flash-attn"}

    def __init__(
        self,
        *,
        binary: str | None,
        model_path: str | None,
        extra_args: tuple[str, ...] = (),
        timeout_sec: int = 120,
        model: str = "whisper_cpp",
        decoder: AudioDecoder | None = None,
        process_runner: ProcessRunner | None = None,
        threads: int = 4,
        gpu_layers: int = 0,
        beam_size: int = 5,
        temperature: float = 0.0,
        prompt_max_chars: int = 512,
    ) -> None:
        self._binary = binary
        self._model_path = model_path
        self._extra_args = self._validate_extra_args(extra_args)
        self._timeout_sec = max(1, timeout_sec)
        self._model = model
        if not 1 <= threads <= 256:
            raise ValueError("whisper.cpp threads must be between 1 and 256")
        if not 0 <= gpu_layers <= 512:
            raise ValueError("whisper.cpp gpu_layers must be between 0 and 512")
        if not 1 <= beam_size <= 20:
            raise ValueError("whisper.cpp beam_size must be between 1 and 20")
        if not math.isfinite(temperature) or not 0 <= temperature <= 2:
            raise ValueError("whisper.cpp temperature must be between 0 and 2")
        if not 0 <= prompt_max_chars <= 8_000:
            raise ValueError("whisper.cpp prompt_max_chars must be between 0 and 8000")
        self._threads = int(threads)
        self._gpu_layers = int(gpu_layers)
        self._beam_size = int(beam_size)
        self._temperature = float(temperature)
        self._prompt_max_chars = int(prompt_max_chars)
        self._decode_limits = AudioDecodeLimits()
        self._decoder = decoder or SafeAudioDecoder(limits=self._decode_limits)
        self._process_runner = process_runner or BoundedSubprocessRunner()

    def name(self) -> str:
        return "whisper_cpp"

    def build_argv(
        self,
        *,
        input_path: str,
        output_path: str,
        language: str | None = None,
        initial_prompt: str | None = None,
    ) -> list[str]:
        if not self._binary:
            raise RuntimeError("whisper.cpp backend unavailable: VOICE_WHISPER_CPP_BIN is not configured")
        if not self._model_path:
            raise RuntimeError("whisper.cpp backend unavailable: VOICE_WHISPER_CPP_MODEL_PATH is not configured")
        argv = [
            self._binary,
            "-m",
            self._model_path,
            "-f",
            input_path,
            "-oj",
            "-of",
            str(Path(output_path).with_suffix("")),
        ]
        if language:
            normalized_language = str(language).strip().lower()
            if not normalized_language.replace("-", "").isalnum() or len(normalized_language) > 16:
                raise ValueError("whisper.cpp language is invalid")
            argv.extend(["-l", normalized_language])
        argv.extend(
            [
                "--threads",
                str(self._threads),
                "--gpu-layers",
                str(self._gpu_layers),
                "--beam-size",
                str(self._beam_size),
                "--temperature",
                f"{self._temperature:g}",
            ]
        )
        prompt = self._bounded_prompt(initial_prompt)
        if prompt:
            argv.extend(["--prompt", prompt])
        argv.extend(self._extra_args)
        return argv

    def transcribe(self, *, filename: str, content: bytes, language: str | None = None) -> TranscriptionResult:
        return self._transcribe(
            filename=filename,
            content=content,
            language=language,
            initial_prompt=None,
            cancellation_token=None,
        )

    def transcribe_with_context(
        self,
        *,
        filename: str,
        content: bytes,
        language: str | None,
        context: dict[str, Any],
    ) -> TranscriptionResult:
        raw_hotwords = context.get("hotwords")
        hotwords: list[object] = raw_hotwords if isinstance(raw_hotwords, list) else []
        classic = str(context.get("classic_transcript") or "")
        previous = str(context.get("previous_segment_context") or "")
        prompt = " ".join([*(str(item) for item in hotwords), classic, previous]).strip()
        return self._transcribe(
            filename=filename,
            content=content,
            language=language,
            initial_prompt=prompt,
            cancellation_token=None,
        )

    def transcribe_with_control(
        self,
        *,
        filename: str,
        content: bytes,
        language: str | None,
        context: dict[str, Any],
        cancellation_token: BackendCancellationToken,
        deadline_monotonic: float,
    ) -> TranscriptionResult:
        del deadline_monotonic
        raw_hotwords = context.get("hotwords")
        hotwords: list[object] = raw_hotwords if isinstance(raw_hotwords, list) else []
        classic = str(context.get("classic_transcript") or "")
        previous = str(context.get("previous_segment_context") or "")
        prompt = " ".join([*(str(item) for item in hotwords), classic, previous]).strip()
        return self._transcribe(
            filename=filename,
            content=content,
            language=language,
            initial_prompt=prompt,
            cancellation_token=cancellation_token,
        )

    def _transcribe(
        self,
        *,
        filename: str,
        content: bytes,
        language: str | None,
        initial_prompt: str | None,
        cancellation_token: BackendCancellationToken | None,
    ) -> TranscriptionResult:
        if cancellation_token is not None:
            cancellation_token.raise_if_cancelled()
        binary, model_path = self._validate_runtime_paths()
        audio = self._decoder.decode(filename=filename, payload=content)
        if cancellation_token is not None:
            cancellation_token.raise_if_cancelled()
        with temporary_audio_workspace() as workspace:
            input_path = workspace.write_bytes(
                "input.wav",
                audio.to_wav_bytes(),
                max_bytes=self._decode_limits.max_decoded_pcm_bytes + 4096,
            )
            output_path = workspace.path_for("transcript.json")
            argv = self.build_argv(
                input_path=str(input_path),
                output_path=str(output_path),
                language=language,
                initial_prompt=initial_prompt,
            )
            argv[0] = binary
            argv[2] = model_path
            try:
                completed = self._process_runner.run(
                    argv,
                    input_payload=b"",
                    max_stdout_bytes=self._MAX_OUTPUT_BYTES,
                    timeout_seconds=(
                        max(
                            0.001,
                            cancellation_token.remaining_seconds(
                                maximum=float(self._timeout_sec)
                            ),
                        )
                        if cancellation_token is not None
                        else self._timeout_sec
                    ),
                    cwd=workspace.root,
                    cancellation_check=(
                        cancellation_token.raise_if_cancelled
                        if cancellation_token is not None
                        else None
                    ),
                )
            except ProcessOutputLimitError as exc:
                raise RuntimeError("whisper.cpp backend output exceeds the configured limit") from exc
            except ProcessPipeError as exc:
                raise RuntimeError("whisper.cpp backend output pipe failed") from exc
            except TimeoutError as exc:
                raise TimeoutError("whisper.cpp backend timeout") from exc
            if cancellation_token is not None:
                cancellation_token.raise_if_cancelled()
            if completed.returncode != 0:
                raise RuntimeError(f"whisper.cpp backend failed with exit code {completed.returncode}")
            try:
                output_bytes = workspace.read_bounded_bytes(
                    output_path.name,
                    max_bytes=self._MAX_OUTPUT_BYTES,
                )
            except ValueError as exc:
                raise RuntimeError("whisper.cpp backend output exceeds the configured limit") from exc
            raw_bytes = output_bytes if output_bytes is not None else completed.stdout
            if len(raw_bytes) > self._MAX_OUTPUT_BYTES:
                raise RuntimeError("whisper.cpp backend output exceeds the configured limit")
            raw = raw_bytes.decode("utf-8", errors="replace")
        return self.parse_json_output(raw, language=language, fallback_duration_ms=audio.duration_ms)

    def _validate_runtime_paths(self) -> tuple[str, str]:
        if not self._binary:
            raise RuntimeError("whisper.cpp backend unavailable: VOICE_WHISPER_CPP_BIN is not configured")
        binary_candidate = shutil.which(self._binary) if not Path(self._binary).is_absolute() else self._binary
        if not binary_candidate:
            raise RuntimeError("whisper.cpp backend unavailable: executable was not found")
        try:
            binary = Path(binary_candidate).expanduser().resolve(strict=True)
        except OSError as exc:
            raise RuntimeError("whisper.cpp backend unavailable: executable was not found") from exc
        if not binary.is_file() or not os.access(binary, os.X_OK):
            raise RuntimeError("whisper.cpp backend unavailable: executable is not runnable")
        if not self._model_path:
            raise RuntimeError("whisper.cpp backend unavailable: VOICE_WHISPER_CPP_MODEL_PATH is not configured")
        try:
            model = Path(self._model_path).expanduser().resolve(strict=True)
        except OSError as exc:
            raise RuntimeError("whisper.cpp backend unavailable: model file was not found") from exc
        if not model.is_file() or not os.access(model, os.R_OK):
            raise RuntimeError("whisper.cpp backend unavailable: model file is not readable")
        return str(binary), str(model)

    @classmethod
    def _validate_extra_args(cls, args: tuple[str, ...]) -> tuple[str, ...]:
        validated: list[str] = []
        index = 0
        while index < len(args):
            flag = str(args[index]).strip()
            if flag in cls._BOOLEAN_FLAGS:
                validated.append(flag)
                index += 1
                continue
            bounds = cls._VALUE_FLAGS.get(flag)
            if bounds is None or index + 1 >= len(args):
                raise ValueError(f"unsupported whisper.cpp argument: {flag or '<empty>'}")
            raw_value = str(args[index + 1]).strip()
            minimum, maximum, converter = bounds
            try:
                value = converter(raw_value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"invalid value for whisper.cpp argument {flag}") from exc
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError(f"value for whisper.cpp argument {flag} must be finite")
            if value < minimum or value > maximum:
                raise ValueError(f"value for whisper.cpp argument {flag} is outside the allowed range")
            validated.extend([flag, raw_value])
            index += 2
        return tuple(validated)

    def audio_chat(self, *, filename: str, content: bytes, context: dict | None = None) -> ChatResult:
        result = self.transcribe(filename=filename, content=content)
        return ChatResult(text=result.text, transcript=result.text, tool_intent=None)

    def list_models(self) -> list[dict]:
        available = False
        try:
            self._validate_runtime_paths()
            available = True
        except RuntimeError:
            available = False
        return [
            {
                "id": self._model,
                "display_name": "whisper.cpp local backend",
                "status": "available" if available else "unavailable",
                "binary_configured": bool(self._binary),
                "model_path_configured": bool(self._model_path),
                "capabilities": [
                    "audio_input",
                    "transcription",
                    "streaming",
                    "offline",
                    "local",
                    "segments",
                ],
            }
        ]

    def context_capabilities(self) -> frozenset[str]:
        return frozenset({"hotwords", "transcript_reference", "previous_segment_context", "language_hint"})

    def create_incremental_recognizer(
        self,
        *,
        filename: str,
        language: str | None,
        max_bytes: int,
    ) -> "WhisperCppIncrementalRecognizer":
        return WhisperCppIncrementalRecognizer(
            backend=self,
            filename=filename,
            language=language,
            max_bytes=max_bytes,
        )

    def _bounded_prompt(self, value: str | None) -> str:
        if self._prompt_max_chars == 0:
            return ""
        normalized = " ".join(str(value or "").replace("\x00", "").split())
        return normalized[: self._prompt_max_chars]

    def parse_json_output(
        self,
        raw: str,
        *,
        language: str | None = None,
        fallback_duration_ms: int | None = None,
    ) -> TranscriptionResult:
        try:
            payload = json.loads(raw or "{}")
        except ValueError:
            text = " ".join((raw or "").split())
            return TranscriptionResult(
                text=text,
                language=language or "und",
                duration_ms=fallback_duration_ms,
                model=self._model,
                warnings=("whisper_cpp_unstructured_output",),
                confidence=None,
                raw_backend="whisper_cpp",
            )
        if not isinstance(payload, dict):
            payload = {}
        segments_payload = payload.get("transcription") or payload.get("segments") or []
        segments = tuple(self._parse_segment(item) for item in segments_payload if isinstance(item, dict))
        text = str(payload.get("text") or " ".join(segment.text for segment in segments)).strip()
        confidence_values = [segment.confidence for segment in segments if segment.confidence is not None]
        confidence = sum(confidence_values) / len(confidence_values) if confidence_values else None
        duration_ms = max((segment.end_ms for segment in segments), default=fallback_duration_ms)
        return TranscriptionResult(
            text=text,
            language=str(payload.get("language") or language or "und"),
            duration_ms=duration_ms,
            model=self._model,
            segments=segments,
            confidence=confidence,
            raw_backend="whisper_cpp",
        )

    @classmethod
    def _parse_segment(cls, item: dict[str, Any]) -> TranscriptionSegment:
        raw_offsets = item.get("offsets")
        raw_timestamps = item.get("timestamps")
        offsets: dict[str, Any] = raw_offsets if isinstance(raw_offsets, dict) else {}
        timestamps: dict[str, Any] = raw_timestamps if isinstance(raw_timestamps, dict) else {}
        start = offsets.get(
            "from",
            item.get("start_ms", item.get("from_ms", item.get("start", item.get("from")))),
        )
        end = offsets.get(
            "to",
            item.get("end_ms", item.get("to_ms", item.get("end", item.get("to")))),
        )
        start_ms = cls._time_to_ms(start, fallback=timestamps.get("from"))
        end_ms = cls._time_to_ms(end, fallback=timestamps.get("to"))
        confidence = cls._normalize_confidence(item.get("confidence", item.get("probability")))
        return TranscriptionSegment(
            start_ms=start_ms,
            end_ms=max(end_ms, start_ms),
            text=str(item.get("text") or "").strip(),
            confidence=confidence,
            backend="whisper_cpp",
        )

    @staticmethod
    def _time_to_ms(value: Any, *, fallback: Any = None) -> int:
        candidate = fallback if value is None or value == "" else value
        if isinstance(candidate, int):
            return max(0, candidate)
        if isinstance(candidate, float):
            return max(0, int(candidate * 1000))
        if isinstance(candidate, str):
            stripped = candidate.strip().replace(",", ".")
            if ":" in stripped:
                try:
                    parts = [float(part) for part in stripped.split(":")]
                    seconds = sum(part * (60**index) for index, part in enumerate(reversed(parts)))
                    return max(0, int(seconds * 1000))
                except ValueError:
                    return 0
            try:
                return max(0, int(float(stripped) * 1000))
            except ValueError:
                return 0
        return 0

    @staticmethod
    def _normalize_confidence(value: Any) -> float | None:
        if value is None:
            return None
        try:
            return max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            return None


class WhisperCppIncrementalRecognizer:
    """Bounded growing-window recognizer for real whisper.cpp partials.

    whisper.cpp has no stable token-stream ABI, so each accepted PCM window is
    re-evaluated by the configured local binary. The composite streaming layer
    owns revision/stability semantics and isolates this model on failure.
    """

    def __init__(
        self,
        *,
        backend: WhisperCppBackend,
        filename: str,
        language: str | None,
        max_bytes: int,
    ) -> None:
        self._backend = backend
        self._filename = filename
        self._language = language
        self._max_bytes = max(2, int(max_bytes))
        self._buffer = bytearray()
        self._latest: TranscriptionResult | None = None
        self._closed = False

    def accept(self, content: bytes) -> str | None:
        if self._closed:
            raise RuntimeError("whisper.cpp stream is closed")
        if not content or len(content) % 2:
            raise ValueError("whisper.cpp PCM chunks must contain complete 16-bit samples")
        if len(self._buffer) + len(content) > self._max_bytes:
            raise ValueError("whisper.cpp stream exceeds its byte budget")
        self._buffer.extend(content)
        self._latest = self._transcribe_window()
        return self._latest.text.strip() or None

    def finish(self) -> TranscriptionResult:
        if self._closed:
            raise RuntimeError("whisper.cpp stream is closed")
        if not self._buffer:
            raise ValueError("whisper.cpp stream contains no audio")
        if self._latest is None:
            self._latest = self._transcribe_window()
        return self._latest

    def close(self) -> None:
        for index in range(len(self._buffer)):
            self._buffer[index] = 0
        self._buffer.clear()
        self._latest = None
        self._closed = True

    def _transcribe_window(self) -> TranscriptionResult:
        output = io.BytesIO()
        with wave.open(output, "wb") as destination:
            destination.setnchannels(1)
            destination.setsampwidth(2)
            destination.setframerate(16_000)
            destination.writeframes(bytes(self._buffer))
        return self._backend.transcribe(
            filename=f"{Path(self._filename).stem or 'stream'}.wav",
            content=output.getvalue(),
            language=self._language,
        )
