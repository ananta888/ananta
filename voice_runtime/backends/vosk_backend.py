from __future__ import annotations

from .base import ChatResult, TranscriptionResult, TranscriptionSegment, VoiceBackend


class VoskBackend(VoiceBackend):
    """Optional Vosk/Kaldi-style backend boundary.

    The adapter is dependency-light by default. If `vosk` or a model path is
    missing it reports unavailable so the router can continue to the next
    configured backend.
    """

    def __init__(self, *, model_path: str | None = None, model: str = "vosk") -> None:
        self._model_path = model_path
        self._model = model

    def name(self) -> str:
        return "vosk"

    def _ensure_available(self) -> None:
        if not self._model_path:
            raise RuntimeError("vosk backend unavailable: VOICE_VOSK_MODEL_PATH is not configured")
        try:
            __import__("vosk")
        except Exception as exc:
            raise RuntimeError("vosk backend unavailable: optional dependency 'vosk' is not installed") from exc

    def transcribe(self, *, filename: str, content: bytes, language: str | None = None) -> TranscriptionResult:
        self._ensure_available()
        text = f"vosk transcript ({filename or 'audio'}) bytes={len(content)}"
        duration_ms = max(50, min(120_000, len(content) * 2))
        return TranscriptionResult(
            text=text,
            language=language or "und",
            duration_ms=duration_ms,
            model=self._model,
            warnings=("vosk_adapter_stub",),
            segments=(
                TranscriptionSegment(
                    start_ms=0,
                    end_ms=duration_ms,
                    text=text,
                    confidence=0.72,
                    backend="vosk",
                ),
            ),
            confidence=0.72,
            raw_backend="vosk",
        )

    def audio_chat(self, *, filename: str, content: bytes, context: dict | None = None) -> ChatResult:
        result = self.transcribe(filename=filename, content=content)
        return ChatResult(text=result.text, transcript=result.text, tool_intent=None)

    def list_models(self) -> list[dict]:
        available = bool(self._model_path)
        return [
            {
                "id": self._model,
                "display_name": "Vosk optional backend",
                "status": "available" if available else "unavailable",
                "model_path": self._model_path,
                "capabilities": ["audio_input", "transcription", "offline", "local"],
            }
        ]
