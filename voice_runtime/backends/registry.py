from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from ..config import VoiceRuntimeConfig
from ..model_manifest import VoiceModelCatalog
from .base import ChatResult, TranscriptionResult, VoiceBackend
from .mock import MockVoiceBackend
from .provenance import with_manifest
from .vosk_backend import VoskBackend
from .voxtral import VoxtralBackend
from .whisper_cpp import WhisperCppBackend

VoiceBackendFactory = Callable[[VoiceRuntimeConfig, VoiceModelCatalog | None], VoiceBackend]


class LazyVoiceBackend(VoiceBackend):
    """Thread-safe proxy that constructs one lightweight backend adapter on demand."""

    def __init__(self, backend_id: str, factory: Callable[[], VoiceBackend]) -> None:
        self._backend_id = backend_id
        self._factory = factory
        self._backend: VoiceBackend | None = None
        self._initialization_lock = threading.Lock()

    def name(self) -> str:
        # Naming and router construction must not initialize an optional engine.
        return self._backend_id

    def transcribe(self, *, filename: str, content: bytes, language: str | None = None) -> TranscriptionResult:
        return self._instance().transcribe(filename=filename, content=content, language=language)

    def audio_chat(self, *, filename: str, content: bytes, context: dict | None = None) -> ChatResult:
        return self._instance().audio_chat(filename=filename, content=content, context=context)

    def list_models(self) -> list[dict]:
        # Adapter constructors are required to stay lightweight. Their health
        # probes may inspect local paths/modules, but must never load weights.
        return self._instance().list_models()

    def context_capabilities(self) -> frozenset[str]:
        return self._instance().context_capabilities()

    def __getattr__(self, name: str) -> Any:
        # Optional, capability-specific methods (for example native streaming)
        # remain discoverable without widening the common backend interface.
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self._instance(), name)

    def _instance(self) -> VoiceBackend:
        backend = self._backend
        if backend is not None:
            return backend
        with self._initialization_lock:
            if self._backend is None:
                self._backend = self._factory()
            return self._backend


class VoiceBackendFactoryRegistry:
    """Extensible factory registry; the router depends only on this abstraction."""

    def __init__(self) -> None:
        self._factories: dict[str, VoiceBackendFactory] = {}

    def register(self, backend_id: str, factory: VoiceBackendFactory, *, replace: bool = False) -> None:
        normalized = _normalize_backend_id(backend_id)
        if normalized in self._factories and not replace:
            raise ValueError(f"voice backend factory is already registered: {normalized}")
        self._factories[normalized] = factory

    def create_lazy(
        self,
        backend_id: str,
        *,
        config: VoiceRuntimeConfig,
        model_catalog: VoiceModelCatalog | None,
    ) -> LazyVoiceBackend:
        normalized = _normalize_backend_id(backend_id)
        factory = self._factories.get(normalized)
        if factory is None:
            raise KeyError(normalized)
        return LazyVoiceBackend(
            normalized,
            lambda: factory(config, model_catalog),
        )

    def registered_ids(self) -> frozenset[str]:
        return frozenset(self._factories)


def build_default_voice_backend_registry() -> VoiceBackendFactoryRegistry:
    registry = VoiceBackendFactoryRegistry()
    registry.register("mock", _build_mock)
    registry.register("vosk", _build_vosk)
    registry.register("whisper_cpp", _build_whisper_cpp)
    registry.register("faster_whisper", _build_faster_whisper)
    registry.register("voxtral", _build_voxtral)
    return registry


def _build_mock(config: VoiceRuntimeConfig, _catalog: VoiceModelCatalog | None) -> VoiceBackend:
    return MockVoiceBackend(model=f"mock-{config.model}")


def _build_vosk(config: VoiceRuntimeConfig, catalog: VoiceModelCatalog | None) -> VoiceBackend:
    return with_manifest(
        VoskBackend(model_path=config.vosk_model_path),
        catalog.get("vosk") if catalog else None,
        device=config.device,
    )


def _build_whisper_cpp(config: VoiceRuntimeConfig, catalog: VoiceModelCatalog | None) -> VoiceBackend:
    return with_manifest(
        WhisperCppBackend(
            binary=config.whisper_cpp_bin,
            model_path=config.whisper_cpp_model_path,
            extra_args=config.whisper_cpp_extra_args,
            timeout_sec=config.timeout_sec,
            threads=config.whisper_cpp_threads,
            gpu_layers=config.whisper_cpp_gpu_layers,
            beam_size=config.whisper_cpp_beam_size,
            temperature=config.whisper_cpp_temperature,
            prompt_max_chars=config.whisper_cpp_prompt_max_chars,
        ),
        catalog.get("whisper_cpp") if catalog else None,
        device=config.device,
    )


def _build_faster_whisper(config: VoiceRuntimeConfig, catalog: VoiceModelCatalog | None) -> VoiceBackend:
    from .faster_whisper import FasterWhisperBackend

    return with_manifest(
        FasterWhisperBackend(
            model_path=config.faster_whisper_model_path,
            device=config.device,
            compute_type=config.faster_whisper_compute_type,
            beam_size=config.faster_whisper_beam_size,
            vad_filter=config.faster_whisper_vad_filter,
            vad_min_silence_ms=config.faster_whisper_vad_min_silence_ms,
            allow_download=config.allow_model_download,
        ),
        catalog.get("faster_whisper") if catalog else None,
        device=config.device,
    )


def _build_voxtral(config: VoiceRuntimeConfig, catalog: VoiceModelCatalog | None) -> VoiceBackend:
    return with_manifest(
        VoxtralBackend(
            model=config.model,
            fallback_model=config.fallback_model,
            preferred_device=config.device,
            model_path=config.model_path,
            runner_path=config.voxtral_runner_path,
            runner_style=config.voxtral_runner_style,
            timeout_sec=config.timeout_sec,
        ),
        catalog.get("voxtral") if catalog else None,
        device=config.device,
    )


def _normalize_backend_id(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if not normalized or len(normalized) > 64 or not normalized.replace("_", "").replace("-", "").isalnum():
        raise ValueError("voice backend id is invalid")
    return normalized
