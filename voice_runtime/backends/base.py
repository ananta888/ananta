from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class TranscriptionSegment:
    start_ms: int
    end_ms: int
    text: str
    confidence: float | None = None
    speaker: str | None = None
    backend: str | None = None
    warnings: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "text": self.text,
            "confidence": self.confidence,
            "speaker": self.speaker,
            "backend": self.backend,
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class TranscriptionResult:
    text: str
    language: str | None = None
    duration_ms: int | None = None
    model: str | None = None
    warnings: tuple[str, ...] = ()
    segments: tuple[TranscriptionSegment, ...] = ()
    pipeline: str | None = None
    confidence: float | None = None
    raw_backend: str | None = None
    rerun_backend: str | None = None
    stages: tuple[dict, ...] = ()

    def with_additional_warnings(self, warnings: list[str]) -> "TranscriptionResult":
        return TranscriptionResult(
            text=self.text,
            language=self.language,
            duration_ms=self.duration_ms,
            model=self.model,
            warnings=tuple([*self.warnings, *warnings]),
            segments=self.segments,
            pipeline=self.pipeline,
            confidence=self.confidence,
            raw_backend=self.raw_backend,
            rerun_backend=self.rerun_backend,
            stages=self.stages,
        )


@dataclass(frozen=True)
class ChatResult:
    text: str
    transcript: str | None = None
    tool_intent: dict | None = None


class VoiceBackend(Protocol):
    def name(self) -> str: ...

    def transcribe(self, *, filename: str, content: bytes, language: str | None = None) -> TranscriptionResult: ...

    def audio_chat(self, *, filename: str, content: bytes, context: dict | None = None) -> ChatResult: ...

    def list_models(self) -> list[dict]: ...
