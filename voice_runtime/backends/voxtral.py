from __future__ import annotations

import hashlib
import os
from typing import Any

from ..audio import normalize_audio_payload
from ..device import detect_runtime_device
from .base import ChatResult, TranscriptionResult, VoiceBackend


class VoxtralBackend(VoiceBackend):
    """
    Voxtral adapter boundary.
    This MVP keeps behavior deterministic and lightweight; real model wiring can
    replace internals without changing runtime API/contract.
    """

    def __init__(self, *, model: str, fallback_model: str, preferred_device: str = "auto", model_path: str | None = None):
        self._model = model
        self._fallback_model = fallback_model
        self._device = detect_runtime_device(preferred_device)
        self._model_path = model_path

    def name(self) -> str:
        return "voxtral"

    def transcribe(self, *, filename: str, content: bytes, language: str | None = None) -> TranscriptionResult:
        self._maybe_fail()
        normalized = normalize_audio_payload(filename=filename, payload=content)
        digest = hashlib.sha1(normalized.payload).hexdigest()[:12]
        text = f"voxtral transcript ({normalized.filename}) sha1={digest}"
        warnings: list[str] = []
        if not normalized.normalization_applied:
            warnings.append("normalization_passthrough")
        if self._model_path:
            warnings.append("model_path_override")
        return TranscriptionResult(
            text=text,
            language=language or "und",
            duration_ms=max(80, min(180_000, len(content) * 2)),
            model=self._model,
            warnings=tuple(warnings),
        )

    def audio_chat(self, *, filename: str, content: bytes, context: dict | None = None) -> ChatResult:
        result = self.transcribe(filename=filename, content=content, language=None)
        intent = self._infer_intent(result.text, context=context)
        return ChatResult(text=result.text, transcript=result.text, tool_intent=intent)

    def list_models(self) -> list[dict]:
        return [
            {
                "id": self._model,
                "display_name": "Voxtral Primary",
                "capabilities": ["audio_input", "transcription", "voice_command", "multimodal_audio_prompt"],
                "device_preference": self._device.get("effective"),
            },
            {
                "id": self._fallback_model,
                "display_name": "Voice Fallback Model",
                "capabilities": ["audio_input", "transcription", "voice_command"],
                "device_preference": "cpu",
            },
        ]

    @staticmethod
    def _infer_intent(text: str, context: dict[str, Any] | None = None) -> dict | None:
        if not text:
            return None
        return {
            "type": "voice_command",
            "confidence": 0.82,
            "source": "voxtral",
            "context_keys": sorted(list((context or {}).keys())),
        }

    @staticmethod
    def _maybe_fail() -> None:
        mode = os.getenv("VOICE_VOXTRAL_MODE", "normal").strip().lower()
        if mode == "unavailable":
            raise RuntimeError("voxtral backend unavailable")
        if mode == "timeout":
            raise TimeoutError("voxtral backend timeout")
