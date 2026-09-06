"""Bind the existing bounded Voice stream runtime to one authorized Meet source."""

import re
import threading
import time
from dataclasses import dataclass, field
from typing import Protocol

from voice_runtime.context import VoiceRecognitionContext
from voice_runtime.streaming import PCM_S16LE_MEDIA_TYPE, BufferedPipelineRecognizer, StreamSession


@dataclass(frozen=True)
class ReceiveBinding:
    tenant_id: str
    project_id: str
    task_id: str
    lease_id: str
    runtime_id: str
    session_id: str
    generation: int
    room_id: str
    membership_epoch: int
    peer_id: str
    own_peer_id: str
    publication_id: str
    publication_epoch: int
    source: str

    def __post_init__(self):
        for name in (
            "tenant_id",
            "project_id",
            "task_id",
            "lease_id",
            "runtime_id",
            "session_id",
            "peer_id",
            "own_peer_id",
            "publication_id",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", value):
                raise ValueError("meet_receive_binding_invalid")
        for name in ("generation", "publication_epoch", "membership_epoch"):
            value = getattr(self, name)
            if type(value) is not int or not 1 <= value < 2**53:
                raise ValueError("meet_receive_binding_invalid")
        if self.peer_id == self.own_peer_id or self.source not in ("microphone", "screen_audio"):
            raise ValueError("meet_receive_source_denied")
        if not isinstance(self.room_id, str) or not re.fullmatch(r"room-[a-f0-9]{18}", self.room_id):
            raise ValueError("meet_receive_room_invalid")


class ReceiveLeasePort(Protocol):
    def require(self, binding: ReceiveBinding) -> None:
        """Require exact current Hub lease AND Meet publisher receive permission."""
        ...


@dataclass(frozen=True)
class ReceivedTranscript:
    binding: ReceiveBinding
    start_sample: int
    end_sample: int
    language: str
    text: str = field(repr=False)


class MeetAudioReceiver:
    """One bounded utterance per Hub assignment, no independent renewal loop.

    Input is canonical mono signed PCM16 little-endian at 16000 Hz. MDS must
    explicitly negotiate/resample to this format, never relabel 48 kHz data.
    """

    def __init__(
        self,
        binding,
        lease: ReceiveLeasePort,
        pipeline,
        *,
        language="de",
        max_audio_seconds=10,
        deadline_monotonic=None,
    ):
        if type(max_audio_seconds) is not int or not 1 <= max_audio_seconds <= 10 or language not in ("de", "en"):
            raise ValueError("meet_receive_profile_invalid")
        deadline = deadline_monotonic if deadline_monotonic is not None else time.monotonic() + 30
        if not time.monotonic() < deadline <= time.monotonic() + 30:
            raise ValueError("meet_receive_deadline_invalid")
        self.binding, self.lease, self.pipeline = binding, lease, pipeline
        self._start_sample = None
        self._next_sample = None
        self._closed = False
        self._operation_lock = threading.Lock()
        recognizer = BufferedPipelineRecognizer(
            pipeline,
            filename="meet-receive",
            language=language,
            max_bytes=max_audio_seconds * 32000,
            media_type=PCM_S16LE_MEDIA_TYPE,
            context=VoiceRecognitionContext(),
        )
        self._stream = StreamSession(
            session_id=binding.session_id,
            recognizer=recognizer,
            media_type=PCM_S16LE_MEDIA_TYPE,
            max_chunk_bytes=3200,
            max_total_bytes=max_audio_seconds * 32000,
            max_audio_seconds=max_audio_seconds,
            max_events=2,
            max_chunks=1000,
            replay_window_chunks=1,
            deadline_monotonic=deadline,
        )

    def push(self, binding, *, start_sample, pcm):
        try:
            with self._operation_lock:
                self._push(binding, start_sample=start_sample, pcm=pcm)
        except Exception:
            self.close()
            raise

    def _push(self, binding, *, start_sample, pcm):
        self._require(binding)
        if (
            type(start_sample) is not int
            or not 0 <= start_sample < 2**53
            or not isinstance(pcm, bytes)
            or not 0 < len(pcm) <= 3200
            or len(pcm) % 320
            or start_sample + len(pcm) // 2 >= 2**53
        ):
            raise ValueError("meet_receive_chunk_invalid")
        if self._next_sample is not None and start_sample != self._next_sample:
            raise ValueError("meet_receive_timeline_invalid")
        if self._start_sample is None:
            self._start_sample = start_sample
        self._next_sample = start_sample + len(pcm) // 2
        self._stream.push(chunk_sequence=self._stream.next_chunk_sequence, content=pcm)

    def finish(self):
        try:
            with self._operation_lock:
                return self._finish()
        finally:
            self.close()

    def _finish(self):
        self._require(self.binding)
        self._stream.finalize()
        self._require(self.binding)
        result = self._stream.result
        if result is None or self._start_sample is None or self._next_sample is None:
            raise ValueError("meet_receive_result_invalid")
        if (
            result.duration_ms != (self._next_sample - self._start_sample) * 1000 // 16_000
            or result.language not in ("de", "en")
            or not isinstance(result.text, str)
            or len(result.text) > 2000
        ):
            raise ValueError("meet_receive_result_invalid")
        return ReceivedTranscript(self.binding, self._start_sample, self._next_sample, result.language, result.text)

    def _require(self, binding):
        if self._closed or binding != self.binding:
            raise ValueError("meet_receive_binding_stale")
        self.lease.require(self.binding)

    def close(self):
        self._closed = True
        try:
            self.pipeline.cancel()
        finally:
            with self._operation_lock:
                self._stream.close()
                self._start_sample = self._next_sample = None
