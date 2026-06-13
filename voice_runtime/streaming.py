from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StreamingCapability:
    enabled: bool
    mode: str
    warning: str | None = None


def resolve_streaming_capability(*, enabled: bool, pipeline: str) -> StreamingCapability:
    if not enabled:
        return StreamingCapability(enabled=False, mode="disabled")
    if pipeline == "realtime_streaming":
        return StreamingCapability(enabled=True, mode="realtime_streaming")
    return StreamingCapability(enabled=False, mode="disabled", warning="streaming_requires_realtime_pipeline")
