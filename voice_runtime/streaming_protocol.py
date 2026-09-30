"""Versioned wire vocabulary of the bounded streaming transcription protocol."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from enum import Enum

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
