"""Versioned, bounded streaming transcription state machine.

The runtime owns audio execution only. Tenant/auth/task ownership stays in the Hub;
runtime session identifiers are opaque capabilities protected by the internal
service token at the HTTP boundary.
"""

from __future__ import annotations

import hashlib
import io
import math
import re
import secrets
import threading
import wave
from dataclasses import asdict, dataclass, field, replace
from enum import Enum
from time import monotonic
from typing import TYPE_CHECKING, Callable, Protocol, cast

from .backends.base import TranscriptionResult, VoiceBackend
from .context import VoiceRecognitionContext
from .execution_policy import VoiceExecutionPolicy
from .preprocessing import AudioDecodeLimits, SafeAudioDecoder
from .preprocessing.audio_decode import AudioDecodeError, AudioDecoder
from .streaming_fusion import (
    IncrementalFusionRecognizer,
    IncrementalFusionUnavailable,
    IncrementalFusionUpdate,
    StreamingModel,
)

if TYPE_CHECKING:
    from .config import VoiceRuntimeConfig

STREAM_SCHEMA_VERSION = "ananta.voice-stream.v1"
PCM_S16LE_MEDIA_TYPE = "audio/pcm;rate=16000;channels=1"
PCM_S16LE_BYTES_PER_SECOND = 16_000 * 2
CONTAINER_MEDIA_TYPES = frozenset({"audio/wav", "audio/webm"})
STREAM_SESSION_ID_PATTERN = re.compile(r"vs_[A-Za-z0-9_-]{22,128}\Z")


@dataclass(frozen=True)
class StreamingCapability:
    enabled: bool
    mode: str
    warning: str | None = None
    schema_version: str = STREAM_SCHEMA_VERSION


def resolve_streaming_capability(*, enabled: bool, pipeline: str) -> StreamingCapability:
    if not enabled:
        return StreamingCapability(enabled=False, mode="disabled")
    if pipeline == "realtime_streaming":
        return StreamingCapability(enabled=True, mode="realtime_streaming")
    return StreamingCapability(enabled=False, mode="disabled", warning="streaming_requires_realtime_pipeline")


class StreamState(str, Enum):
    CREATED = "created"
    ACTIVE = "active"
    FINALIZING = "finalizing"
    FINAL = "final"
    FAILED = "failed"
    CLOSED = "closed"


@dataclass(frozen=True)
class StreamProtocolError(Exception):
    code: str
    message: str
    status_code: int = 400
    retriable: bool = False


@dataclass(frozen=True)
class StreamEvent:
    sequence: int
    event_type: str
    payload: dict[str, object]
    schema_version: str = STREAM_SCHEMA_VERSION

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class TranscriptRevisionEvent:
    turn_id: str
    revision: int
    text: str
    authority: str
    final: bool
    emitted_at_ms: int
    segment_closed: bool = False


class StreamingTranscriptTracker:
    """Bounded monotone projection of backend partial/final revisions.

    Segment rotation is deliberately independent from live partial delivery.
    The tracker owns no audio and schedules no correction task.
    """

    def __init__(self, *, live_partials: bool, max_turns: int = 512) -> None:
        if not 1 <= max_turns <= 4096:
            raise ValueError("streaming_transcript_turn_budget_invalid")
        self._live_partials = bool(live_partials)
        self._max_turns = max_turns
        self._latest: dict[str, TranscriptRevisionEvent] = {}
        self._finalized: set[str] = set()
        self._buffered_partial: dict[str, TranscriptRevisionEvent] = {}
        self._drops = {"stale": 0, "duplicate": 0, "after_final": 0}

    def ingest(
        self,
        *,
        turn_id: str,
        revision: int,
        text: str,
        final: bool,
        observed_at_ms: int,
        segment_closed: bool = False,
    ) -> TranscriptRevisionEvent | None:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", turn_id):
            raise ValueError("streaming_transcript_turn_invalid")
        if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
            raise ValueError("streaming_transcript_revision_invalid")
        if not isinstance(text, str) or len(text.encode("utf-8")) > 65_536:
            raise ValueError("streaming_transcript_text_invalid")
        if turn_id in self._finalized:
            self._drops["after_final"] += 1
            return None
        prior = self._latest.get(turn_id)
        if prior is not None and revision <= prior.revision:
            self._drops["duplicate" if revision == prior.revision else "stale"] += 1
            return None
        event = TranscriptRevisionEvent(
            turn_id=turn_id,
            revision=revision,
            text=text,
            authority="final" if final else "provisional",
            final=bool(final),
            emitted_at_ms=max(0, int(observed_at_ms)),
            segment_closed=bool(segment_closed),
        )
        self._latest[turn_id] = event
        if final:
            self._finalized.add(turn_id)
            self._buffered_partial.pop(turn_id, None)
        elif not self._live_partials:
            self._buffered_partial[turn_id] = event
            self._trim()
            return None
        self._trim()
        return event

    def snapshot(self) -> dict[str, object]:
        return {
            "turns": tuple(self._latest.values()),
            "finalized": len(self._finalized),
            "buffered_partials": len(self._buffered_partial),
            "drops": dict(self._drops),
            "timers": 0,
        }

    def _trim(self) -> None:
        while len(self._latest) > self._max_turns:
            oldest = next(iter(self._latest))
            self._latest.pop(oldest, None)
            self._finalized.discard(oldest)
            self._buffered_partial.pop(oldest, None)


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


@dataclass
class StreamSession:
    session_id: str
    recognizer: IncrementalRecognizer | None
    media_type: str
    max_chunk_bytes: int
    max_total_bytes: int
    max_audio_seconds: float
    max_events: int
    max_chunks: int
    replay_window_chunks: int
    deadline_monotonic: float
    state: StreamState = StreamState.CREATED
    next_chunk_sequence: int = 0
    total_bytes: int = 0
    event_sequence: int = 0
    events: list[StreamEvent] = field(default_factory=list)
    result: TranscriptionResult | None = None
    chunk_digests: dict[int, str] = field(default_factory=dict)
    execution_policy: dict[str, object] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _inflight: bool = field(default=False, repr=False)

    def push(self, *, chunk_sequence: int, content: bytes) -> StreamEvent:
        if not self._lock.acquire(blocking=False):
            raise StreamProtocolError("stream.backpressure", "another chunk is still being processed", 429, True)
        try:
            if self._inflight:
                raise StreamProtocolError("stream.backpressure", "another chunk is still being processed", 429, True)
            self._inflight = True
            self._check_deadline()
            if self.state not in {StreamState.CREATED, StreamState.ACTIVE}:
                raise StreamProtocolError("stream.invalid_state", f"cannot add chunks in state {self.state.value}", 409)
            if not content:
                raise StreamProtocolError("stream.empty_chunk", "audio chunk must not be empty", 422)
            if len(content) > self.max_chunk_bytes:
                raise StreamProtocolError("stream.chunk_too_large", "audio chunk exceeds its byte budget", 413)
            digest = hashlib.sha256(content).hexdigest()
            if chunk_sequence < self.next_chunk_sequence:
                accepted_digest = self.chunk_digests.get(chunk_sequence)
                if accepted_digest == digest:
                    return self._append_event(
                        "chunk_replayed",
                        {"chunk_sequence": chunk_sequence, "next_chunk_sequence": self.next_chunk_sequence},
                    )
                if accepted_digest is None:
                    raise StreamProtocolError(
                        "stream.replay_window_expired",
                        "replayed chunk is outside the bounded replay window",
                        409,
                    )
                raise StreamProtocolError("stream.chunk_conflict", "replayed chunk content differs", 409)
            if chunk_sequence != self.next_chunk_sequence:
                raise StreamProtocolError(
                    "stream.sequence_gap",
                    f"expected chunk {self.next_chunk_sequence}, received {chunk_sequence}",
                    409,
                    True,
                )
            if self.total_bytes + len(content) > self.max_total_bytes:
                raise StreamProtocolError("stream.total_too_large", "stream exceeds its byte budget", 413)
            if self.next_chunk_sequence >= self.max_chunks:
                raise StreamProtocolError(
                    "stream.chunk_limit_exceeded",
                    "stream exceeds its chunk-count budget",
                    413,
                )

            recognizer = self._active_recognizer()
            try:
                partial = recognizer.accept(content)
            except IncrementalFusionUnavailable as exc:
                self.state = StreamState.FAILED
                raise StreamProtocolError(
                    "stream.models_unavailable",
                    "all incremental streaming models failed",
                    503,
                    True,
                ) from exc
            self.chunk_digests[chunk_sequence] = digest
            self.next_chunk_sequence += 1
            oldest_retained = max(0, self.next_chunk_sequence - self.replay_window_chunks)
            for accepted_sequence in tuple(self.chunk_digests):
                if accepted_sequence < oldest_retained:
                    self.chunk_digests.pop(accepted_sequence, None)
            self.total_bytes += len(content)
            self.state = StreamState.ACTIVE
            payload: dict[str, object] = {
                "chunk_sequence": chunk_sequence,
                "next_chunk_sequence": self.next_chunk_sequence,
                "accepted_bytes": len(content),
                "total_bytes": self.total_bytes,
                "max_audio_seconds": self.max_audio_seconds,
            }
            if isinstance(partial, IncrementalFusionUpdate):
                payload.update(partial.as_payload())
                return self._append_event("partial_fusion", payload)
            if partial:
                payload["text"] = partial
                return self._append_event("partial", payload)
            return self._append_event("chunk_accepted", payload)
        except Exception:
            if self.state is StreamState.FINALIZING:
                self.state = StreamState.FAILED
            if self.state is StreamState.FAILED:
                self._release_audio_state()
            raise
        finally:
            self._inflight = False
            self._lock.release()

    def finalize(self) -> StreamEvent:
        with self._lock:
            self._check_deadline()
            if self.state is StreamState.FINAL and self.result is not None:
                return self._append_event("final_replayed", {"result": self.result.as_dict()})
            if self.state not in {StreamState.ACTIVE, StreamState.CREATED}:
                raise StreamProtocolError("stream.invalid_state", f"cannot finalize state {self.state.value}", 409)
            self.state = StreamState.FINALIZING
            try:
                recognizer = self._active_recognizer()
                remaining_seconds = max(0.001, self.deadline_monotonic - monotonic())
                tighten_deadline = getattr(recognizer, "tighten_deadline", None)
                if callable(tighten_deadline):
                    tighten_deadline(remaining_seconds)
                result = recognizer.finish()
                self._enforce_decoded_audio_duration(result)
                self.result = result
                self._check_deadline()
            except IncrementalFusionUnavailable as exc:
                self.state = StreamState.FAILED
                raise StreamProtocolError(
                    "stream.models_unavailable",
                    "all incremental streaming models failed",
                    503,
                    True,
                ) from exc
            except Exception:
                self.state = StreamState.FAILED
                raise
            finally:
                self._release_audio_state()
            self.state = StreamState.FINAL
            return self._append_event("final", {"result": self.result.as_dict()})

    def close(self) -> None:
        with self._lock:
            if self.state is StreamState.CLOSED:
                return
            try:
                self._release_audio_state()
            finally:
                self.state = StreamState.CLOSED
                self.events.clear()
                self.chunk_digests.clear()
                self.result = None
                self.execution_policy.clear()

    def snapshot(self, *, after_event: int = -1) -> dict[str, object]:
        with self._lock:
            self._check_deadline()
            selected = [event.as_dict() for event in self.events if event.sequence > after_event]
            return {
                "schema_version": STREAM_SCHEMA_VERSION,
                "session_id": self.session_id,
                "state": self.state.value,
                "media_type": self.media_type,
                "next_chunk_sequence": self.next_chunk_sequence,
                "total_bytes": self.total_bytes,
                "max_audio_seconds": self.max_audio_seconds,
                "events": selected,
                "result": self.result.as_dict() if self.result else None,
                "execution_policy": dict(self.execution_policy),
            }

    def _append_event(self, event_type: str, payload: dict[str, object]) -> StreamEvent:
        event = StreamEvent(sequence=self.event_sequence, event_type=event_type, payload=payload)
        self.event_sequence += 1
        self.events.append(event)
        if len(self.events) > self.max_events:
            del self.events[: len(self.events) - self.max_events]
        return event

    def _check_deadline(self) -> None:
        if monotonic() > self.deadline_monotonic:
            self.state = StreamState.FAILED
            self._release_audio_state()
            raise StreamProtocolError("stream.deadline_exceeded", "stream deadline exceeded", 504, False)

    def _active_recognizer(self) -> IncrementalRecognizer:
        recognizer = self.recognizer
        if recognizer is None:
            raise StreamProtocolError("stream.invalid_state", "stream recognizer is unavailable", 409)
        return recognizer

    def _enforce_decoded_audio_duration(self, result: TranscriptionResult) -> None:
        """Verify opaque container duration once the runtime has decoded it.

        Raw PCM is bounded exactly while chunks are accepted. Encoded container
        byte length is not a safe duration proxy, so its final transcription
        contract must contain the duration measured from decoded audio.
        """

        if self.media_type not in CONTAINER_MEDIA_TYPES:
            return
        duration_ms = result.duration_ms
        if isinstance(duration_ms, bool) or duration_ms is None:
            raise StreamProtocolError(
                "stream.audio_duration_unavailable",
                "decoded audio duration is unavailable",
                502,
            )
        try:
            normalized_duration_ms = float(duration_ms)
        except (TypeError, ValueError) as exc:
            raise StreamProtocolError(
                "stream.audio_duration_unavailable",
                "decoded audio duration is unavailable",
                502,
            ) from exc
        if not math.isfinite(normalized_duration_ms) or normalized_duration_ms < 0:
            raise StreamProtocolError(
                "stream.audio_duration_unavailable",
                "decoded audio duration is unavailable",
                502,
            )
        if normalized_duration_ms > self.max_audio_seconds * 1_000:
            raise StreamProtocolError(
                "stream.audio_duration_exceeded",
                "decoded audio exceeds the stream duration budget",
                413,
            )

    def _release_audio_state(self) -> None:
        """Drop audio-derived state and close the recognizer exactly once."""

        recognizer = self.recognizer
        self.recognizer = None
        self.chunk_digests.clear()
        if recognizer is not None:
            recognizer.close()


class StreamSessionManager:
    def __init__(
        self,
        recognizer_factory: RecognizerFactory,
        *,
        policy_recognizer_factory: PolicyRecognizerFactory | None = None,
        max_sessions: int = 8,
        max_chunk_bytes: int = 1_048_576,
        max_total_bytes: int = 25 * 1024 * 1024,
        max_events: int = 128,
        max_chunks_per_session: int = 65_536,
        replay_window_chunks: int = 256,
        default_deadline_seconds: float = 120.0,
        default_max_audio_seconds: float | None = None,
        max_decoded_pcm_bytes: int = 64 * 1024 * 1024,
        audio_decode_timeout_seconds: int = 30,
        container_audio_decoder_factory: ContainerAudioDecoderFactory | None = None,
    ) -> None:
        self._recognizer_factory = recognizer_factory
        self._policy_recognizer_factory = policy_recognizer_factory
        self._max_sessions = max(1, max_sessions)
        self._max_chunk_bytes = max(1, max_chunk_bytes)
        self._max_total_bytes = max(1, max_total_bytes)
        self._max_events = max(4, max_events)
        self._max_chunks_per_session = max(1, max_chunks_per_session)
        self._replay_window_chunks = max(1, min(replay_window_chunks, self._max_chunks_per_session))
        self._default_deadline_seconds = max(1.0, default_deadline_seconds)
        self._default_max_audio_seconds = max(
            0.001,
            float(default_max_audio_seconds or (self._max_total_bytes / (16_000 * 2))),
        )
        self._max_decoded_pcm_bytes = max(2, int(max_decoded_pcm_bytes))
        self._audio_decode_timeout_seconds = max(1, int(audio_decode_timeout_seconds))
        self._container_audio_decoder_factory = container_audio_decoder_factory or self._create_container_audio_decoder
        self._sessions: dict[str, StreamSession] = {}
        self._lock = threading.Lock()

    def create(
        self,
        *,
        filename: str,
        language: str | None,
        media_type: str,
        deadline_seconds: float | None = None,
        max_audio_seconds: float | None = None,
        requested_session_id: str | None = None,
        recognition_context: VoiceRecognitionContext | None = None,
        execution_policy: dict[str, object] | None = None,
    ) -> StreamSession:
        if media_type not in {PCM_S16LE_MEDIA_TYPE, *CONTAINER_MEDIA_TYPES}:
            raise StreamProtocolError("stream.unsupported_media_type", "unsupported stream media type", 415)
        requested_deadline = min(
            self._default_deadline_seconds,
            max(1.0, float(deadline_seconds or self._default_deadline_seconds)),
        )
        requested_audio_seconds = float(
            self._default_max_audio_seconds if max_audio_seconds is None else max_audio_seconds
        )
        if not math.isfinite(requested_audio_seconds) or requested_audio_seconds <= 0:
            raise StreamProtocolError(
                "stream.invalid_audio_budget",
                "stream audio duration budget must be positive",
                422,
            )
        effective_audio_seconds = min(requested_audio_seconds, self._default_max_audio_seconds)
        effective_max_bytes = self._max_total_bytes
        if media_type == PCM_S16LE_MEDIA_TYPE:
            pcm_byte_budget = int(effective_audio_seconds * PCM_S16LE_BYTES_PER_SECOND)
            if pcm_byte_budget < 1:
                raise StreamProtocolError(
                    "stream.invalid_audio_budget",
                    "stream audio duration budget is smaller than one PCM byte",
                    422,
                )
            effective_max_bytes = min(
                self._max_total_bytes,
                pcm_byte_budget,
            )
        validated_session_id = _validate_requested_session_id(requested_session_id)
        started_monotonic = monotonic()
        with self._lock:
            self._cleanup_locked()
            session_id = validated_session_id or self._new_session_id_locked()
            if session_id in self._sessions:
                raise StreamProtocolError(
                    "stream.session_id_conflict",
                    "stream session identifier is already reserved",
                    409,
                )
            terminal_states = {StreamState.FINAL, StreamState.FAILED, StreamState.CLOSED}
            active = sum(session.state not in terminal_states for session in self._sessions.values())
            if active >= self._max_sessions:
                raise StreamProtocolError("stream.capacity_exhausted", "stream session capacity exhausted", 429, True)
            container_decoder = (
                self._container_audio_decoder_factory(
                    effective_audio_seconds,
                    effective_max_bytes,
                )
                if media_type in CONTAINER_MEDIA_TYPES
                else None
            )
            recognizer = (
                self._policy_recognizer_factory(
                    filename,
                    language,
                    effective_max_bytes,
                    media_type,
                    recognition_context,
                )
                if recognition_context is not None and self._policy_recognizer_factory is not None
                else self._recognizer_factory(filename, language, effective_max_bytes, media_type)
            )
            if media_type in CONTAINER_MEDIA_TYPES:
                if container_decoder is None:
                    recognizer.close()
                    raise StreamProtocolError(
                        "stream.audio_preflight_unavailable",
                        "container audio preflight is unavailable",
                        503,
                        True,
                    )
                recognizer = ContainerPreflightRecognizer(
                    recognizer,
                    decoder=container_decoder,
                    filename=filename,
                    max_bytes=effective_max_bytes,
                    max_audio_seconds=effective_audio_seconds,
                )
            remaining_deadline = requested_deadline - (monotonic() - started_monotonic)
            if remaining_deadline <= 0:
                recognizer.close()
                raise StreamProtocolError(
                    "stream.deadline_exceeded",
                    "stream deadline exceeded during model loading",
                    504,
                    False,
                )
            session = StreamSession(
                session_id=session_id,
                recognizer=recognizer,
                media_type=media_type,
                max_chunk_bytes=self._max_chunk_bytes,
                max_total_bytes=effective_max_bytes,
                max_audio_seconds=effective_audio_seconds,
                max_events=self._max_events,
                max_chunks=self._max_chunks_per_session,
                replay_window_chunks=self._replay_window_chunks,
                deadline_monotonic=monotonic() + remaining_deadline,
                execution_policy=dict(execution_policy or {}),
            )
            session._append_event(
                "created",
                {
                    "next_chunk_sequence": 0,
                    "max_audio_seconds": effective_audio_seconds,
                    "max_total_bytes": effective_max_bytes,
                    "execution_policy": dict(execution_policy or {}),
                },
            )
            self._sessions[session_id] = session
            return session

    def _new_session_id_locked(self) -> str:
        while True:
            session_id = f"vs_{secrets.token_urlsafe(24)}"
            if session_id not in self._sessions:
                return session_id

    def _create_container_audio_decoder(
        self,
        max_audio_seconds: float,
        max_encoded_bytes: int,
    ) -> AudioDecoder:
        # ffmpeg applies ``-t`` as a hard output cap. Decode one extra
        # millisecond so an over-budget container cannot be mistaken for an
        # exact-boundary recording after truncation.
        max_duration_ms = max(2, math.ceil(max_audio_seconds * 1_000) + 1)
        return SafeAudioDecoder(
            limits=AudioDecodeLimits(
                max_encoded_bytes=max_encoded_bytes,
                max_decoded_pcm_bytes=self._max_decoded_pcm_bytes,
                max_duration_ms=max_duration_ms,
                ffmpeg_timeout_sec=self._audio_decode_timeout_seconds,
            )
        )

    def get(self, session_id: str) -> StreamSession:
        with self._lock:
            self._cleanup_locked()
            session = self._sessions.get(session_id)
        if session is None:
            raise StreamProtocolError("stream.not_found", "stream session not found", 404)
        return session

    def delete(self, session_id: str) -> bool:
        with self._lock:
            session = self._sessions.pop(session_id, None)
        if session is None:
            return False
        session.close()
        return True

    def _cleanup_locked(self) -> None:
        if not self._sessions:
            return
        now = monotonic()
        expired = [
            session_id
            for session_id, session in self._sessions.items()
            if session.deadline_monotonic < now or session.state is StreamState.CLOSED
        ]
        for session_id in expired:
            self._sessions.pop(session_id).close()


def buffered_recognizer_factory(backend: VoiceBackend) -> RecognizerFactory:
    return lambda filename, language, max_bytes, _media_type: BufferedBatchRecognizer(
        backend,
        filename=filename,
        language=language,
        max_bytes=max_bytes,
    )


def container_safe_recognizer_factory(
    backend: VoiceBackend,
    incremental_factory: RecognizerFactory | None,
) -> RecognizerFactory:
    """Keep container inputs on batch execution after the decode preflight."""

    buffered_factory = buffered_recognizer_factory(backend)
    if incremental_factory is None:
        return buffered_factory

    def create(
        filename: str,
        language: str | None,
        max_bytes: int,
        media_type: str,
    ) -> IncrementalRecognizer:
        factory = buffered_factory if media_type in CONTAINER_MEDIA_TYPES else incremental_factory
        return factory(filename, language, max_bytes, media_type)

    return create


def _validate_requested_session_id(requested_session_id: str | None) -> str | None:
    if requested_session_id is None:
        return None
    if not isinstance(requested_session_id, str) or STREAM_SESSION_ID_PATTERN.fullmatch(requested_session_id) is None:
        raise StreamProtocolError(
            "stream.invalid_session_id",
            "requested stream session identifier is invalid",
            422,
        )
    return requested_session_id


def buffered_pipeline_recognizer_factory(pipeline: TranscriptionPipelinePort) -> PolicyRecognizerFactory:
    return lambda filename, language, max_bytes, media_type, context: BufferedPipelineRecognizer(
        pipeline,
        filename=filename,
        language=language,
        max_bytes=max_bytes,
        media_type=media_type,
        context=context,
    )


def policy_streaming_recognizer_factory(
    pipeline: TranscriptionPipelinePort,
    backend_catalog: IncrementalBackendCatalog,
    runtime_config: VoiceRuntimeConfig,
) -> PolicyRecognizerFactory:
    """Build Hub-selected incremental execution with a bounded final fallback.

    Single recognition may expose provisional output from the selected primary
    backend, while finalization always traverses the normal Hub-policy pipeline.
    Multi-model fusion retains its native incremental final contract.  Opaque
    containers and unavailable incremental adapters stay on bounded batch
    execution and never emit fake partials.
    """

    fallback = buffered_pipeline_recognizer_factory(pipeline)

    def create(
        filename: str,
        language: str | None,
        max_bytes: int,
        media_type: str,
        context: VoiceRecognitionContext,
    ) -> IncrementalRecognizer:
        policy = VoiceExecutionPolicy.resolve(runtime_config, context.configuration)
        enabled = (
            media_type == PCM_S16LE_MEDIA_TYPE and policy.source == "hub_context" and policy.transport_mode == "stream"
        )
        if not enabled:
            return fallback(filename, language, max_bytes, media_type, context)

        if policy.recognition_strategy == "single":
            available = backend_catalog.available_backends((policy.primary_backend,))
            backend = available.get(policy.primary_backend)
            factory = getattr(backend, "create_incremental_recognizer", None)
            if callable(factory):
                try:
                    partial_recognizer = factory(
                        filename=filename,
                        language=language,
                        max_bytes=max_bytes,
                    )
                except Exception:
                    partial_recognizer = None
                if partial_recognizer is not None:
                    final_recognizer = fallback(filename, language, max_bytes, media_type, context)
                    return IncrementalPrimaryPipelineRecognizer(
                        partial_recognizer,
                        cast(BufferedPipelineRecognizer, final_recognizer),
                    )
            return fallback(filename, language, max_bytes, media_type, context)

        fusion_enabled = policy.recognition_strategy in {"parallel_compare", "parallel_fusion"} and bool(
            policy.feature_flags.get("voice_fusion", False)
        )
        if not fusion_enabled:
            return fallback(filename, language, max_bytes, media_type, context)

        requested = tuple(dict.fromkeys((policy.primary_backend, *policy.secondary_backends)))[
            : policy.max_parallel_backends
        ]
        available = backend_catalog.available_backends(requested)
        models: list[StreamingModel] = []
        for backend_id in requested:
            backend = available.get(backend_id)
            factory = getattr(backend, "create_incremental_recognizer", None)
            if not callable(factory):
                continue
            try:
                recognizer = factory(filename=filename, language=language, max_bytes=max_bytes)
                models.append(StreamingModel(backend_id=backend_id, recognizer=recognizer))
            except Exception:
                continue
        if len(models) >= 2:
            return cast(IncrementalRecognizer, IncrementalFusionRecognizer(tuple(models)))
        for model in models:
            try:
                model.recognizer.close()
            except Exception:
                pass
        return fallback(filename, language, max_bytes, media_type, context)

    return create


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
