from __future__ import annotations

import os
from dataclasses import dataclass


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _as_int(value: str | None, default: int) -> int:
    try:
        return int(str(value).strip())
    except Exception:
        return default


@dataclass(frozen=True)
class VoiceRuntimeConfig:
    host: str = "0.0.0.0"
    port: int = 8090
    provider: str = "voice-runtime"
    backend: str = "mock"
    model: str = "voxtral"
    fallback_model: str = "whisper-small"
    timeout_sec: int = 120
    max_audio_mb: int = 25
    enable_streaming: bool = False
    store_audio: bool = False
    device: str = "auto"
    model_path: str | None = None
    backend_fallback_order: tuple[str, ...] = ("voxtral", "mock")

    @classmethod
    def from_env(cls) -> "VoiceRuntimeConfig":
        return cls(
            host=os.getenv("VOICE_RUNTIME_HOST", "0.0.0.0").strip() or "0.0.0.0",
            port=_as_int(os.getenv("VOICE_RUNTIME_PORT"), 8090),
            provider=os.getenv("VOICE_PROVIDER", "voice-runtime").strip() or "voice-runtime",
            backend=os.getenv("VOICE_RUNTIME_BACKEND", "mock").strip() or "mock",
            model=os.getenv("VOICE_MODEL", "voxtral").strip() or "voxtral",
            fallback_model=os.getenv("VOICE_FALLBACK_MODEL", "whisper-small").strip() or "whisper-small",
            timeout_sec=max(1, _as_int(os.getenv("VOICE_TIMEOUT_SEC"), 120)),
            max_audio_mb=max(1, _as_int(os.getenv("VOICE_MAX_AUDIO_MB"), 25)),
            enable_streaming=_as_bool(os.getenv("VOICE_ENABLE_STREAMING"), False),
            store_audio=_as_bool(os.getenv("VOICE_STORE_AUDIO"), False),
            device=os.getenv("VOICE_RUNTIME_DEVICE", "auto").strip() or "auto",
            model_path=(os.getenv("VOICE_RUNTIME_MODEL_PATH", "").strip() or None),
            backend_fallback_order=tuple(
                item.strip()
                for item in (os.getenv("VOICE_BACKEND_FALLBACK_ORDER", "voxtral,mock").split(","))
                if item.strip()
            )
            or ("voxtral", "mock"),
        )
