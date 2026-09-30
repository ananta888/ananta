"""Incremental recognizer adapters used by streaming sessions.

Each adapter buffers audio within an explicit byte budget and executes one
bounded recognition at finalization; none of them schedules work elsewhere.
"""

from __future__ import annotations

import io
import math
import wave
from dataclasses import replace
from typing import Callable, Protocol

from .backends.base import TranscriptionResult, VoiceBackend
from .context import VoiceRecognitionContext
from .preprocessing.audio_decode import AudioDecodeError, AudioDecoder
from .streaming_fusion import IncrementalFusionUpdate
from .streaming_protocol import PCM_S16LE_MEDIA_TYPE, StreamProtocolError


class IncrementalRecognizer(Protocol):
    def accept(self, content: bytes) -> str | IncrementalFusionUpdate | None: ...

    def finish(self) -> TranscriptionResult: ...

    def close(self) -> None: ...


class BufferedBatchRecognizer:
    """Compatibility recognizer for non-streaming backends.

    It emits no fake partial text and delegates one bounded batch only at finalization.
    """

    def __init__(self, backend: VoiceBackend, *, filename: str, language: str | None, max_bytes: int) -> None:
        self._backend = backend
        self._filename = filename
        self._language = language
        self._max_bytes = max_bytes
        self._buffer = bytearray()

    def accept(self, content: bytes) -> str | None:
        if len(self._buffer) + len(content) > self._max_bytes:
            raise StreamProtocolError("stream.total_too_large", "stream exceeds its byte budget", 413)
        self._buffer.extend(content)
        return None

    def finish(self) -> TranscriptionResult:
        if not self._buffer:
            raise StreamProtocolError("stream.empty", "stream contains no audio", 422)
        return self._backend.transcribe(
            filename=self._filename,
            content=bytes(self._buffer),
            language=self._language,
        )

    def close(self) -> None:
        for index in range(len(self._buffer)):
            self._buffer[index] = 0
        self._buffer.clear()


RecognizerFactory = Callable[[str, str | None, int, str], IncrementalRecognizer]
PolicyRecognizerFactory = Callable[
    [str, str | None, int, str, VoiceRecognitionContext],
    IncrementalRecognizer,
]
ContainerAudioDecoderFactory = Callable[[float, int], AudioDecoder]


class TranscriptionPipelinePort(Protocol):
    def transcribe(
        self,
        *,
        filename: str,
        content: bytes,
        language: str | None = None,
        context: VoiceRecognitionContext | None = None,
    ) -> TranscriptionResult: ...


class IncrementalBackendCatalog(Protocol):
    def available_backends(self, backend_ids: tuple[str, ...] | None = None) -> dict[str, VoiceBackend]: ...


class BufferedPipelineRecognizer:
    """Hub-policy stream adapter; final execution uses the normal pipeline."""

    def __init__(
        self,
        pipeline: TranscriptionPipelinePort,
        *,
        filename: str,
        language: str | None,
        max_bytes: int,
        media_type: str,
        context: VoiceRecognitionContext,
    ) -> None:
        self._pipeline = pipeline
        self._filename = filename
        self._language = language
        self._max_bytes = max_bytes
        self._media_type = media_type
        self._context: VoiceRecognitionContext | None = context
        self._buffer = bytearray()

    def accept(self, content: bytes) -> str | None:
        if len(self._buffer) + len(content) > self._max_bytes:
            raise StreamProtocolError("stream.total_too_large", "stream exceeds its byte budget", 413)
        self._buffer.extend(content)
        return None

    def finish(self) -> TranscriptionResult:
        if not self._buffer:
            raise StreamProtocolError("stream.empty", "stream contains no audio", 422)
        content = bytes(self._buffer)
        filename = self._filename
        if self._media_type == PCM_S16LE_MEDIA_TYPE:
            content = _pcm_s16le_to_wav(content)
            filename = f"{filename}.wav"
        context = self._context
        if context is None:
            raise StreamProtocolError("stream.invalid_state", "stream policy context is unavailable", 409)
        try:
            return self._pipeline.transcribe(
                filename=filename,
                content=content,
                language=self._language,
                context=context,
            )
        finally:
            self._context = None

    def tighten_deadline(self, remaining_seconds: float) -> None:
        """Narrow the immutable Hub policy to the stream's remaining lifetime."""

        context = self._context
        if context is None or context.configuration is None:
            return
        configuration = context.configuration
        self._context = replace(
            context,
            configuration=replace(
                configuration,
                candidate_deadline_sec=max(
                    0.001,
                    min(configuration.candidate_deadline_sec, float(remaining_seconds)),
                ),
            ),
        )

    def close(self) -> None:
        for index in range(len(self._buffer)):
            self._buffer[index] = 0
        self._buffer.clear()
        self._context = None


class IncrementalPrimaryPipelineRecognizer:
    """Emit primary-backend partials while preserving Hub-policy finalization.

    The native recognizer is deliberately limited to provisional output.  The
    buffered pipeline remains the source of the final result so correction,
    provenance, review metadata, and future policy stages keep their normal
    contract.  A partial-recognizer failure degrades to final-only operation;
    it must not discard an otherwise valid recording.
    """

    def __init__(
        self,
        partial_recognizer: IncrementalRecognizer,
        final_recognizer: BufferedPipelineRecognizer,
    ) -> None:
        self._partial_recognizer: IncrementalRecognizer | None = partial_recognizer
        self._final_recognizer = final_recognizer

    def accept(self, content: bytes) -> str | IncrementalFusionUpdate | None:
        if len(content) % 2:
            raise StreamProtocolError(
                "stream.invalid_pcm",
                "PCM stream chunks must contain complete signed 16-bit samples",
                422,
            )
        self._final_recognizer.accept(content)
        recognizer = self._partial_recognizer
        if recognizer is None:
            return None
        try:
            return recognizer.accept(content)
        except Exception:
            # Partials are an optional projection of the authoritative buffered
            # final.  Fail closed to final-only operation without exposing model
            # details or losing already accepted audio.
            self._close_partial_recognizer()
            return None

    def finish(self) -> TranscriptionResult:
        self._close_partial_recognizer()
        return self._final_recognizer.finish()

    def tighten_deadline(self, remaining_seconds: float) -> None:
        self._final_recognizer.tighten_deadline(remaining_seconds)

    def close(self) -> None:
        self._close_partial_recognizer()
        self._final_recognizer.close()

    def _close_partial_recognizer(self) -> None:
        recognizer = self._partial_recognizer
        self._partial_recognizer = None
        if recognizer is not None:
            try:
                recognizer.close()
            except Exception:
                pass


class ContainerPreflightRecognizer:
    """Buffer opaque containers and validate decoded duration before inference."""

    _DURATION_LIMIT_CODES = frozenset({"decode.duration_limit"})

    def __init__(
        self,
        recognizer: IncrementalRecognizer,
        *,
        decoder: AudioDecoder,
        filename: str,
        max_bytes: int,
        max_audio_seconds: float,
    ) -> None:
        self._recognizer: IncrementalRecognizer | None = recognizer
        self._decoder = decoder
        self._filename = filename
        self._max_bytes = max_bytes
        self._max_audio_seconds = max_audio_seconds
        self._buffer = bytearray()

    def accept(self, content: bytes) -> None:
        if len(self._buffer) + len(content) > self._max_bytes:
            raise StreamProtocolError("stream.total_too_large", "stream exceeds its byte budget", 413)
        self._buffer.extend(content)
        return None

    def finish(self) -> TranscriptionResult:
        if not self._buffer:
            raise StreamProtocolError("stream.empty", "stream contains no audio", 422)
        content = bytes(self._buffer)
        try:
            decoded = self._decoder.decode(filename=self._filename, payload=content)
        except AudioDecodeError as exc:
            if exc.code in self._DURATION_LIMIT_CODES:
                raise StreamProtocolError(
                    "stream.audio_duration_exceeded",
                    "decoded audio exceeds the stream duration budget",
                    413,
                ) from exc
            raise StreamProtocolError(
                "stream.invalid_audio",
                "container audio failed strict decode validation",
                422,
            ) from exc
        except TimeoutError as exc:
            raise StreamProtocolError(
                "stream.audio_preflight_timeout",
                "container audio preflight timed out",
                504,
            ) from exc
        except Exception as exc:
            raise StreamProtocolError(
                "stream.audio_preflight_unavailable",
                "container audio preflight is unavailable",
                503,
                True,
            ) from exc
        duration_ms = decoded.duration_ms
        if isinstance(duration_ms, bool) or not isinstance(duration_ms, int) or duration_ms < 0:
            raise StreamProtocolError(
                "stream.audio_duration_unavailable",
                "decoded audio duration is unavailable",
                502,
            )
        allowed_frames = math.floor(self._max_audio_seconds * decoded.sample_rate_hz)
        if decoded.frame_count > allowed_frames:
            raise StreamProtocolError(
                "stream.audio_duration_exceeded",
                "decoded audio exceeds the stream duration budget",
                413,
            )
        recognizer = self._active_recognizer()
        recognizer.accept(content)
        result = recognizer.finish()
        return replace(result, duration_ms=duration_ms)

    def close(self) -> None:
        for index in range(len(self._buffer)):
            self._buffer[index] = 0
        self._buffer.clear()
        recognizer = self._recognizer
        self._recognizer = None
        if recognizer is not None:
            recognizer.close()

    def tighten_deadline(self, remaining_seconds: float) -> None:
        recognizer = self._active_recognizer()
        tighten_deadline = getattr(recognizer, "tighten_deadline", None)
        if callable(tighten_deadline):
            tighten_deadline(remaining_seconds)

    def _active_recognizer(self) -> IncrementalRecognizer:
        recognizer = self._recognizer
        if recognizer is None:
            raise StreamProtocolError("stream.invalid_state", "stream recognizer is unavailable", 409)
        return recognizer


def _pcm_s16le_to_wav(content: bytes) -> bytes:
    if len(content) % 2:
        raise StreamProtocolError("stream.invalid_pcm", "PCM stream must contain complete signed 16-bit samples", 422)
    output = io.BytesIO()
    with wave.open(output, "wb") as destination:
        destination.setnchannels(1)
        destination.setsampwidth(2)
        destination.setframerate(16_000)
        destination.writeframes(content)
    return output.getvalue()
