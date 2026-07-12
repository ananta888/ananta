from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Lock

import pytest

from voice_runtime.app import create_app
from voice_runtime.backends.base import ChatResult, TranscriptionResult
from voice_runtime.backends.registry import VoiceBackendFactoryRegistry
from voice_runtime.backends.router import build_voice_backend_resolver, build_voice_backend_router
from voice_runtime.config import VoiceRuntimeConfig
from voice_runtime.errors import BackendUnavailableError


class _ExtensionBackend:
    def name(self) -> str:
        return "extension"

    def transcribe(self, *, filename: str, content: bytes, language: str | None = None) -> TranscriptionResult:
        return TranscriptionResult(text="extension", language=language, raw_backend="extension")

    def audio_chat(self, *, filename: str, content: bytes, context: dict | None = None) -> ChatResult:
        return ChatResult(text="extension", transcript="extension")

    def list_models(self) -> list[dict]:
        return [{"id": "extension", "status": "available", "capabilities": ["transcription"]}]

    def context_capabilities(self) -> frozenset[str]:
        return frozenset()


class _UnavailableBackend(_ExtensionBackend):
    def __init__(self) -> None:
        self.calls = 0

    def name(self) -> str:
        return "unavailable"

    def transcribe(self, *, filename: str, content: bytes, language: str | None = None) -> TranscriptionResult:
        self.calls += 1
        raise BackendUnavailableError("backend unavailable")


def test_custom_backend_can_be_registered_without_router_changes() -> None:
    registry = VoiceBackendFactoryRegistry()
    registry.register("extension", lambda _config, _catalog: _ExtensionBackend())
    config = replace(VoiceRuntimeConfig(), backend_fallback_order=("extension",))

    router = build_voice_backend_router(config, registry=registry)

    assert router.transcribe(filename="sample.wav", content=b"audio").text == "extension"


def test_parallel_first_access_constructs_the_adapter_exactly_once() -> None:
    registry = VoiceBackendFactoryRegistry()
    factory_calls = 0
    call_lock = Lock()

    def factory(_config, _catalog):
        nonlocal factory_calls
        with call_lock:
            factory_calls += 1
        return _ExtensionBackend()

    registry.register("extension", factory)
    config = replace(VoiceRuntimeConfig(), backend_fallback_order=("extension",))
    router = build_voice_backend_router(config, registry=registry)

    with ThreadPoolExecutor(max_workers=8) as executor:
        models = list(executor.map(lambda _: router.list_models(), range(24)))

    assert all(items[0]["id"] == "extension" for items in models)
    assert factory_calls == 1


def test_router_name_does_not_initialize_adapter() -> None:
    registry = VoiceBackendFactoryRegistry()
    factory_calls = 0

    def factory(_config, _catalog):
        nonlocal factory_calls
        factory_calls += 1
        return _ExtensionBackend()

    registry.register("extension", factory)
    config = replace(VoiceRuntimeConfig(), backend_fallback_order=("extension",))
    router = build_voice_backend_router(config, registry=registry)

    assert router.name() == "extension"
    assert factory_calls == 0


def test_routed_resolver_reuses_one_lazy_instance_and_route() -> None:
    registry = VoiceBackendFactoryRegistry()
    factory_calls = 0

    def factory(_config, _catalog):
        nonlocal factory_calls
        factory_calls += 1
        return _ExtensionBackend()

    registry.register("extension", factory)
    config = replace(VoiceRuntimeConfig(), backend_fallback_order=("extension",))
    resolver = build_voice_backend_resolver(
        config,
        backend_ids=("extension",),
        registry=registry,
    )

    first = resolver.resolve("extension")
    second = resolver.resolve("extension")

    assert first is second
    assert first is resolver.route(("extension",))
    assert first.transcribe(filename="first.wav", content=b"audio").text == "extension"
    assert second.transcribe(filename="second.wav", content=b"audio").text == "extension"
    assert factory_calls == 1


def test_routed_resolver_rejects_unknown_backend_without_fallback() -> None:
    registry = VoiceBackendFactoryRegistry()
    registry.register("extension", lambda _config, _catalog: _ExtensionBackend())
    resolver = build_voice_backend_resolver(
        VoiceRuntimeConfig(),
        backend_ids=("extension",),
        registry=registry,
    )

    with pytest.raises(ValueError, match="unsupported ASR backend: missing"):
        resolver.resolve("missing")


def test_resolved_and_fallback_routes_share_circuit_breaker_state() -> None:
    unavailable = _UnavailableBackend()
    registry = VoiceBackendFactoryRegistry()
    registry.register("unavailable", lambda _config, _catalog: unavailable)
    registry.register("extension", lambda _config, _catalog: _ExtensionBackend())
    resolver = build_voice_backend_resolver(
        VoiceRuntimeConfig(),
        backend_ids=("unavailable", "extension"),
        registry=registry,
    )
    selected = resolver.resolve("unavailable")

    for _attempt in range(3):
        with pytest.raises(BackendUnavailableError):
            selected.transcribe(filename="sample.wav", content=b"audio")

    result = resolver.route(("unavailable", "extension")).transcribe(
        filename="sample.wav",
        content=b"audio",
    )

    assert result.text == "extension"
    assert result.decision_trace["fallback_attempts"][0]["reason_code"] == "circuit_open"
    assert unavailable.calls == 3


def test_app_injects_the_same_long_lived_resolver_into_runtime_wiring() -> None:
    app = create_app(
        VoiceRuntimeConfig(
            backend_fallback_order=("mock",),
            primary_backend="mock",
            asr_backend="mock",
        )
    )
    resolver = app.config["voice_runtime_backend_resolver"]

    assert resolver.resolve("mock") is app.config["voice_runtime_backend"]
    assert app.config["voice_runtime_pipeline"]._backend_resolver is resolver
