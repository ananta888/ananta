from __future__ import annotations

from dataclasses import dataclass

from ..config import VoiceRuntimeConfig
from .base import ChatResult, TranscriptionResult, VoiceBackend
from .mock import MockVoiceBackend
from .vosk_backend import VoskBackend
from .voxtral import VoxtralBackend
from .whisper_cpp import WhisperCppBackend


@dataclass(frozen=True)
class _BackendEntry:
    backend_id: str
    backend: VoiceBackend


class RoutedVoiceBackend(VoiceBackend):
    """Fallback-capable backend router with explicit fallback warnings."""

    def __init__(self, entries: list[_BackendEntry]):
        if not entries:
            raise RuntimeError("voice backend router requires at least one backend")
        self._entries = entries

    def name(self) -> str:
        return self._entries[0].backend.name()

    def list_models(self) -> list[dict]:
        models: list[dict] = []
        for entry in self._entries:
            models.extend(entry.backend.list_models())
        return models

    def transcribe(self, *, filename: str, content: bytes, language: str | None = None) -> TranscriptionResult:
        last_exc: Exception | None = None
        for index, entry in enumerate(self._entries):
            try:
                result = entry.backend.transcribe(filename=filename, content=content, language=language)
                warnings = list(result.warnings)
                if index > 0:
                    warnings.append(f"fallback_backend:{entry.backend_id}")
                return TranscriptionResult(
                    text=result.text,
                    language=result.language,
                    duration_ms=result.duration_ms,
                    model=result.model,
                    warnings=tuple(warnings),
                    segments=result.segments,
                    pipeline=result.pipeline,
                    confidence=result.confidence,
                    raw_backend=result.raw_backend or entry.backend_id,
                    rerun_backend=result.rerun_backend,
                    stages=result.stages,
                )
            except Exception as exc:
                last_exc = exc
        if last_exc:
            raise last_exc
        raise RuntimeError("voice backend routing failed")

    def audio_chat(self, *, filename: str, content: bytes, context: dict | None = None) -> ChatResult:
        last_exc: Exception | None = None
        for entry in self._entries:
            try:
                return entry.backend.audio_chat(filename=filename, content=content, context=context)
            except Exception as exc:
                last_exc = exc
        if last_exc:
            raise last_exc
        raise RuntimeError("voice backend routing failed")


def build_voice_backend_router(config: VoiceRuntimeConfig) -> RoutedVoiceBackend:
    entries: list[_BackendEntry] = []
    for backend_id in config.backend_fallback_order:
        normalized = str(backend_id or "").strip().lower()
        if normalized == "voxtral":
            entries.append(
                _BackendEntry(
                    backend_id="voxtral",
                    backend=VoxtralBackend(
                        model=config.model,
                        fallback_model=config.fallback_model,
                        preferred_device=config.device,
                        model_path=config.model_path,
                    ),
                )
            )
        elif normalized == "mock":
            entries.append(_BackendEntry(backend_id="mock", backend=MockVoiceBackend(model=f"mock-{config.model}")))
        elif normalized == "vosk":
            entries.append(_BackendEntry(backend_id="vosk", backend=VoskBackend(model_path=config.vosk_model_path)))
        elif normalized == "whisper_cpp":
            entries.append(
                _BackendEntry(
                    backend_id="whisper_cpp",
                    backend=WhisperCppBackend(
                        binary=config.whisper_cpp_bin,
                        model_path=config.whisper_cpp_model_path,
                        extra_args=config.whisper_cpp_extra_args,
                        timeout_sec=config.timeout_sec,
                    ),
                )
            )

    if not entries:
        raise RuntimeError(f"unsupported VOICE_BACKEND_FALLBACK_ORDER: {config.backend_fallback_order}")
    return RoutedVoiceBackend(entries=entries)
