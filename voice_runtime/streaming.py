"""Versioned, bounded streaming transcription state machine.

The runtime owns audio execution only. Tenant/auth/task ownership stays in the Hub;
runtime session identifiers are opaque capabilities protected by the internal
service token at the HTTP boundary.

This module is the public entry point and owns the session registry
(``StreamSessionManager``). Protocol vocabulary, the transcript tracker,
recognizer adapters, the per-session state machine and recognizer factories
live in focused ``streaming_*`` modules and are re-exported here.
"""

from __future__ import annotations

import math
import secrets
import threading
from time import monotonic

from .context import VoiceRecognitionContext
from .preprocessing import AudioDecodeLimits, SafeAudioDecoder
from .preprocessing.audio_decode import AudioDecoder
from .streaming_protocol import (
    CONTAINER_MEDIA_TYPES,
    PCM_S16LE_BYTES_PER_SECOND,
    PCM_S16LE_MEDIA_TYPE,
    STREAM_SCHEMA_VERSION,
    STREAM_SESSION_ID_PATTERN,
    StreamEvent,
    StreamingCapability,
    StreamProtocolError,
    StreamState,
    resolve_streaming_capability,
)
from .streaming_recognizer_factories import (
    buffered_pipeline_recognizer_factory,
    buffered_recognizer_factory,
    container_safe_recognizer_factory,
    policy_streaming_recognizer_factory,
)
from .streaming_recognizers import (
    BufferedBatchRecognizer,
    BufferedPipelineRecognizer,
    ContainerAudioDecoderFactory,
    ContainerPreflightRecognizer,
    IncrementalBackendCatalog,
    IncrementalPrimaryPipelineRecognizer,
    IncrementalRecognizer,
    PolicyRecognizerFactory,
    RecognizerFactory,
    TranscriptionPipelinePort,
    _pcm_s16le_to_wav,
)
from .streaming_session import StreamSession
from .streaming_transcript import StreamingTranscriptTracker, TranscriptRevisionEvent

__all__ = [
    "CONTAINER_MEDIA_TYPES",
    "PCM_S16LE_BYTES_PER_SECOND",
    "PCM_S16LE_MEDIA_TYPE",
    "STREAM_SCHEMA_VERSION",
    "STREAM_SESSION_ID_PATTERN",
    "BufferedBatchRecognizer",
    "BufferedPipelineRecognizer",
    "ContainerAudioDecoderFactory",
    "ContainerPreflightRecognizer",
    "IncrementalBackendCatalog",
    "IncrementalPrimaryPipelineRecognizer",
    "IncrementalRecognizer",
    "PolicyRecognizerFactory",
    "RecognizerFactory",
    "StreamEvent",
    "StreamProtocolError",
    "StreamSession",
    "StreamSessionManager",
    "StreamState",
    "StreamingCapability",
    "StreamingTranscriptTracker",
    "TranscriptRevisionEvent",
    "TranscriptionPipelinePort",
    "buffered_pipeline_recognizer_factory",
    "buffered_recognizer_factory",
    "container_safe_recognizer_factory",
    "policy_streaming_recognizer_factory",
    "resolve_streaming_capability",
    "_pcm_s16le_to_wav",
    "_validate_requested_session_id",
]


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
