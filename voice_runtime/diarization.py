from __future__ import annotations

from dataclasses import replace
from typing import Protocol

from .backends.base import TranscriptionSegment


class DiarizationProcessor(Protocol):
    def name(self) -> str: ...

    def assign(self, segments: tuple[TranscriptionSegment, ...]) -> tuple[TranscriptionSegment, ...]: ...


class MockDiarizationProcessor(DiarizationProcessor):
    def name(self) -> str:
        return "mock"

    def assign(self, segments: tuple[TranscriptionSegment, ...]) -> tuple[TranscriptionSegment, ...]:
        return tuple(
            replace(segment, speaker=segment.speaker or f"SPEAKER_{index % 2 + 1:02d}")
            for index, segment in enumerate(segments)
        )


def build_diarization_processor(backend: str) -> DiarizationProcessor | None:
    normalized = str(backend or "none").strip().lower()
    if normalized in {"", "none", "off", "disabled"}:
        return None
    if normalized == "mock":
        return MockDiarizationProcessor()
    raise ValueError(f"unsupported VOICE_DIARIZATION_BACKEND: {normalized}")
