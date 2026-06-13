from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class AudioSegment:
    filename: str
    content: bytes
    start_ms: int = 0
    end_ms: int | None = None
    warnings: tuple[str, ...] = ()

    @property
    def duration_ms(self) -> int:
        if self.end_ms is not None:
            return max(0, self.end_ms - self.start_ms)
        return max(50, min(180_000, len(self.content) * 2))


class VadProcessor(Protocol):
    def name(self) -> str: ...

    def split(self, *, filename: str, content: bytes) -> list[AudioSegment]: ...


class MockVadProcessor(VadProcessor):
    def name(self) -> str:
        return "mock"

    def split(self, *, filename: str, content: bytes) -> list[AudioSegment]:
        return [
            AudioSegment(
                filename=filename or "audio",
                content=content,
                start_ms=0,
                end_ms=max(50, min(180_000, len(content) * 2)),
            )
        ]


class OptionalBackendVadProcessor(VadProcessor):
    def __init__(self, *, backend: str) -> None:
        self._backend = backend

    def name(self) -> str:
        return self._backend

    def split(self, *, filename: str, content: bytes) -> list[AudioSegment]:
        raise RuntimeError(f"vad backend unavailable: optional backend '{self._backend}' is not installed")


def build_vad_processor(backend: str) -> VadProcessor:
    normalized = str(backend or "mock").strip().lower()
    if normalized in {"", "mock", "none", "passthrough"}:
        return MockVadProcessor()
    if normalized in {"webrtcvad", "silero"}:
        return OptionalBackendVadProcessor(backend=normalized)
    raise ValueError(f"unsupported VOICE_VAD_BACKEND: {normalized}")
