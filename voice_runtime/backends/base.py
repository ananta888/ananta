from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class TranscriptionResult:
    text: str
    language: str | None = None
    duration_ms: int | None = None
    model: str | None = None
    warnings: tuple[str, ...] = ()


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
