"""Single bounded streaming session state machine.

A session owns its chunk sequencing, replay window, event log and deadline;
the audio itself is held only by the session's recognizer.
"""

from __future__ import annotations

import hashlib
import math
import threading
from dataclasses import dataclass, field
from time import monotonic

from .backends.base import TranscriptionResult
from .streaming_fusion import IncrementalFusionUnavailable, IncrementalFusionUpdate
from .streaming_protocol import (
    CONTAINER_MEDIA_TYPES,
    STREAM_SCHEMA_VERSION,
    StreamEvent,
    StreamProtocolError,
    StreamState,
)
from .streaming_recognizers import IncrementalRecognizer


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
