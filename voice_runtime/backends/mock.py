from __future__ import annotations

import hashlib
import os
import time

from .base import ChatResult, TranscriptionResult, VoiceBackend


class MockVoiceBackend(VoiceBackend):
    """
    Deterministic mock backend for CI and local tests without model downloads.

    VOICE_MOCK_MODE:
    - normal (default)
    - timeout
    - unavailable
    """

    def __init__(self, *, model: str = "mock-whisper-small") -> None:
        self._model = model

    def name(self) -> str:
        return "mock"

    def transcribe(self, *, filename: str, content: bytes, language: str | None = None) -> TranscriptionResult:
        self._maybe_fail()
        digest = hashlib.sha1(content).hexdigest()[:12]
        text = f"mock transcript ({filename or 'audio'}) sha1={digest}"
        return TranscriptionResult(
            text=text,
            language=language or "und",
            duration_ms=max(50, min(120_000, len(content) * 2)),
            model=self._model,
            warnings=(),
        )

    def audio_chat(self, *, filename: str, content: bytes, context: dict | None = None) -> ChatResult:
        result = self.transcribe(filename=filename, content=content, language=None)
        intent = {"type": "voice_command", "confidence": 0.75, "source": "mock"} if context else None
        return ChatResult(text=result.text, transcript=result.text, tool_intent=intent)

    def list_models(self) -> list[dict]:
        return [
            {
                "id": self._model,
                "display_name": "Mock Voice Backend",
                "capabilities": ["audio_input", "transcription", "voice_command"],
                "device_preference": "cpu",
            }
        ]

    @staticmethod
    def _maybe_fail() -> None:
        mode = os.getenv("VOICE_MOCK_MODE", "normal").strip().lower()
        if mode == "timeout":
            time.sleep(1.25)
            raise TimeoutError("mock backend timeout")
        if mode == "unavailable":
            raise RuntimeError("mock backend unavailable")
