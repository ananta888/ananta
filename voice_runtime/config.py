from __future__ import annotations

import os
from dataclasses import dataclass

PIPELINES = frozenset({
    "simple",
    "oldschool_light",
    "whisper_cpp",
    "realtime_streaming",
    "meeting",
    "confidence_rerun",
    "custom",
})
ASR_BACKENDS = frozenset({"mock", "voxtral", "vosk", "whisper_cpp"})
VAD_BACKENDS = frozenset({"mock", "none", "passthrough", "webrtcvad", "silero"})
POSTPROCESS_BACKENDS = frozenset({"none", "off", "disabled", "rules", "rule_based", "glossary", "llm", "llm_corrector"})
DIARIZATION_BACKENDS = frozenset({"none", "off", "disabled", "mock"})


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _as_int(value: str | None, default: int) -> int:
    try:
        return int(str(value).strip())
    except Exception:
        return default


def _as_float(value: str | None, default: float) -> float:
    try:
        return float(str(value).strip())
    except Exception:
        return default


def _choice(value: str | None, *, default: str, allowed: frozenset[str], name: str) -> str:
    normalized = str(value or default).strip().lower() or default
    if normalized not in allowed:
        raise ValueError(f"unsupported {name}: {normalized}")
    return normalized


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
    transcription_pipeline: str = "simple"
    vad_backend: str = "mock"
    asr_backend: str = "mock"
    postprocess_backend: str = "none"
    confidence_rerun_enabled: bool = False
    confidence_threshold: float = 0.7
    rerun_backend: str = "mock"
    rerun_max_segments: int = 3
    diarization_backend: str = "none"
    glossary_path: str | None = None
    vosk_model_path: str | None = None
    whisper_cpp_bin: str | None = None
    whisper_cpp_model_path: str | None = None
    whisper_cpp_extra_args: tuple[str, ...] = ()

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
            transcription_pipeline=_choice(
                os.getenv("VOICE_TRANSCRIPTION_PIPELINE"),
                default="simple",
                allowed=PIPELINES,
                name="VOICE_TRANSCRIPTION_PIPELINE",
            ),
            vad_backend=_choice(
                os.getenv("VOICE_VAD_BACKEND"),
                default="mock",
                allowed=VAD_BACKENDS,
                name="VOICE_VAD_BACKEND",
            ),
            asr_backend=_choice(
                os.getenv("VOICE_ASR_BACKEND"),
                default="mock",
                allowed=ASR_BACKENDS,
                name="VOICE_ASR_BACKEND",
            ),
            postprocess_backend=_choice(
                os.getenv("VOICE_POSTPROCESS_BACKEND"),
                default="none",
                allowed=POSTPROCESS_BACKENDS,
                name="VOICE_POSTPROCESS_BACKEND",
            ),
            confidence_rerun_enabled=_as_bool(os.getenv("VOICE_CONFIDENCE_RERUN_ENABLED"), False),
            confidence_threshold=max(0.0, min(1.0, _as_float(os.getenv("VOICE_CONFIDENCE_THRESHOLD"), 0.7))),
            rerun_backend=os.getenv("VOICE_RERUN_BACKEND", "mock").strip().lower() or "mock",
            rerun_max_segments=max(0, _as_int(os.getenv("VOICE_RERUN_MAX_SEGMENTS"), 3)),
            diarization_backend=_choice(
                os.getenv("VOICE_DIARIZATION_BACKEND"),
                default="none",
                allowed=DIARIZATION_BACKENDS,
                name="VOICE_DIARIZATION_BACKEND",
            ),
            glossary_path=os.getenv("VOICE_GLOSSARY_PATH", "").strip() or None,
            vosk_model_path=(
                os.getenv("VOICE_VOSK_MODEL_PATH", "").strip()
                or os.getenv("VOICE_RUNTIME_MODEL_PATH", "").strip()
                or None
            ),
            whisper_cpp_bin=os.getenv("VOICE_WHISPER_CPP_BIN", "").strip() or None,
            whisper_cpp_model_path=os.getenv("VOICE_WHISPER_CPP_MODEL_PATH", "").strip() or None,
            whisper_cpp_extra_args=tuple(
                item.strip()
                for item in os.getenv("VOICE_WHISPER_CPP_EXTRA_ARGS", "").split()
                if item.strip()
            ),
        )
