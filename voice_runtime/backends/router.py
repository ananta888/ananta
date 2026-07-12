from __future__ import annotations

import threading
import time
from dataclasses import dataclass, replace
from typing import Callable, cast

from ..config import VoiceRuntimeConfig
from ..errors import FALLBACK_ERROR_CODES, normalize_backend_exception
from ..execution_control import BackendCancellationToken
from ..metrics import VoiceRuntimeMetricsPort
from ..model_manifest import VoiceModelCatalog
from ..resources import BackendResourceRequirement, backend_resource_requirement
from .base import ChatResult, TranscriptionResult, VoiceBackend
from .registry import VoiceBackendFactoryRegistry, build_default_voice_backend_registry


@dataclass(frozen=True)
class _BackendEntry:
    backend_id: str
    backend: VoiceBackend


@dataclass
class _CircuitState:
    failures: int = 0
    opened_at: float | None = None
    half_open_inflight: bool = False


class _SharedCircuitRegistry:
    """Own circuit state shared by every routed view of a backend catalog."""

    def __init__(self) -> None:
        self.states: dict[str, _CircuitState] = {}
        self.lock = threading.RLock()

    def register(self, backend_ids: tuple[str, ...]) -> None:
        with self.lock:
            for backend_id in backend_ids:
                self.states.setdefault(backend_id, _CircuitState())


class RoutedVoiceBackend(VoiceBackend):
    """Fallback-capable backend router with explicit fallback warnings."""

    def __init__(
        self,
        entries: list[_BackendEntry],
        *,
        failure_threshold: int = 3,
        cooldown_seconds: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
        metrics: VoiceRuntimeMetricsPort | None = None,
        circuit_registry: _SharedCircuitRegistry | None = None,
    ):
        if not entries:
            raise RuntimeError("voice backend router requires at least one backend")
        self._entries = entries
        self._failure_threshold = max(1, int(failure_threshold))
        self._cooldown_seconds = max(0.0, float(cooldown_seconds))
        self._clock = clock
        self._metrics = metrics
        shared_circuits = circuit_registry or _SharedCircuitRegistry()
        shared_circuits.register(tuple(entry.backend_id for entry in entries))
        self._circuits = shared_circuits.states
        self._circuit_lock = shared_circuits.lock

    def name(self) -> str:
        return str(self._entries[0].backend.name())

    def list_models(self) -> list[dict]:
        models: list[dict] = []
        for entry in self._entries:
            try:
                models.extend(
                    {**item, "engine": str(item.get("engine") or entry.backend_id)}
                    for item in entry.backend.list_models()
                )
            except Exception:
                models.append(
                    {
                        "id": entry.backend_id,
                        "display_name": entry.backend_id,
                        "capabilities": [],
                        "status": "unavailable",
                    }
                )
        return models

    def context_capabilities(self) -> frozenset[str]:
        backend = self._entries[0].backend
        method = getattr(backend, "context_capabilities", None)
        return frozenset(method()) if callable(method) else frozenset()

    def available_backends(self, backend_ids: tuple[str, ...] | None = None) -> dict[str, VoiceBackend]:
        requested = set(backend_ids or tuple(entry.backend_id for entry in self._entries))
        return {
            entry.backend_id: entry.backend
            for entry in self._entries
            if entry.backend_id in requested and self._circuit_may_recover(entry.backend_id)
        }

    def streaming_recognizer_factory(self, filename: str, language: str | None, max_bytes: int, media_type: str):
        if media_type == "audio/pcm;rate=16000;channels=1":
            for entry in self._entries:
                factory = getattr(entry.backend, "create_incremental_recognizer", None)
                if callable(factory) and self._circuit_may_recover(entry.backend_id):
                    return factory(filename=filename, language=language, max_bytes=max_bytes)
        from ..streaming import BufferedBatchRecognizer

        return BufferedBatchRecognizer(
            self,
            filename=filename,
            language=language,
            max_bytes=max_bytes,
        )

    def transcribe(self, *, filename: str, content: bytes, language: str | None = None) -> TranscriptionResult:
        return self._transcribe(
            filename=filename,
            content=content,
            language=language,
            context={},
            cancellation_token=None,
        )

    def transcribe_with_control(
        self,
        *,
        filename: str,
        content: bytes,
        language: str | None,
        context: dict[str, object],
        cancellation_token: BackendCancellationToken,
        deadline_monotonic: float,
    ) -> TranscriptionResult:
        del deadline_monotonic
        return self._transcribe(
            filename=filename,
            content=content,
            language=language,
            context=context,
            cancellation_token=cancellation_token,
        )

    def _transcribe(
        self,
        *,
        filename: str,
        content: bytes,
        language: str | None,
        context: dict[str, object],
        cancellation_token: BackendCancellationToken | None,
    ) -> TranscriptionResult:
        last_exc: Exception | None = None
        attempts: list[dict[str, str]] = []
        for index, entry in enumerate(self._entries):
            if cancellation_token is not None:
                cancellation_token.raise_if_cancelled()
            if not self._acquire_circuit_attempt(entry.backend_id):
                attempts.append({"backend": entry.backend_id, "status": "skipped", "reason_code": "circuit_open"})
                if self._metrics is not None:
                    self._metrics.observe_fallback(backend=entry.backend_id, reason_code="unavailable")
                continue
            started = self._clock()
            try:
                controlled = getattr(entry.backend, "transcribe_with_control", None)
                if cancellation_token is not None and callable(controlled):
                    result = cast(
                        TranscriptionResult,
                        controlled(
                            filename=filename,
                            content=content,
                            language=language,
                            context=context,
                            cancellation_token=cancellation_token,
                            deadline_monotonic=cancellation_token.deadline_monotonic,
                        ),
                    )
                elif context and callable(
                    contextual := getattr(entry.backend, "transcribe_with_context", None)
                ):
                    result = contextual(
                        filename=filename,
                        content=content,
                        language=language,
                        context=context,
                    )
                else:
                    result = entry.backend.transcribe(
                        filename=filename,
                        content=content,
                        language=language,
                    )
                if cancellation_token is not None:
                    cancellation_token.raise_if_cancelled()
                self._record_success(entry.backend_id)
                if self._metrics is not None:
                    self._metrics.observe_backend_call(
                        operation="transcribe",
                        backend=entry.backend_id,
                        outcome="succeeded",
                        duration_seconds=self._clock() - started,
                    )
                attempts.append({"backend": entry.backend_id, "status": "succeeded", "reason_code": "ok"})
                warnings = list(result.warnings)
                if index > 0:
                    warnings.append(f"fallback_backend:{entry.backend_id}")
                return replace(
                    result,
                    warnings=tuple(warnings),
                    raw_backend=result.raw_backend or entry.backend_id,
                    decision_trace={
                        **dict(result.decision_trace),
                        "fallback_attempts": attempts,
                        "fallback_allowed_error_codes": sorted(FALLBACK_ERROR_CODES),
                    },
                )
            except Exception as exc:
                normalized = normalize_backend_exception(exc)
                last_exc = normalized
                self._record_failure(entry.backend_id)
                if self._metrics is not None:
                    self._metrics.observe_backend_call(
                        operation="transcribe",
                        backend=entry.backend_id,
                        outcome=normalized.code,
                        duration_seconds=self._clock() - started,
                    )
                    self._metrics.observe_fallback(backend=entry.backend_id, reason_code=normalized.code)
                attempts.append({"backend": entry.backend_id, "status": "failed", "reason_code": normalized.code})
                if normalized.code not in FALLBACK_ERROR_CODES:
                    raise normalized from exc
        if last_exc:
            raise last_exc
        raise RuntimeError("voice backend routing failed")

    def cancel_transcription(
        self,
        *,
        cancellation_token: BackendCancellationToken,
    ) -> None:
        for entry in self._entries:
            callback = getattr(entry.backend, "cancel_transcription", None)
            if callable(callback):
                try:
                    callback(cancellation_token=cancellation_token)
                except Exception:
                    continue

    def resource_requirements(self) -> BackendResourceRequirement:
        requirements = tuple(
            backend_resource_requirement(entry.backend) for entry in self._entries
        )
        return BackendResourceRequirement(
            ram_bytes=max((item.ram_bytes for item in requirements), default=0),
            vram_bytes=max((item.vram_bytes for item in requirements), default=0),
            concurrency_slots=max(
                (item.concurrency_slots for item in requirements),
                default=1,
            ),
        )

    def audio_chat(self, *, filename: str, content: bytes, context: dict | None = None) -> ChatResult:
        last_exc: Exception | None = None
        for entry in self._entries:
            if not self._acquire_circuit_attempt(entry.backend_id):
                if self._metrics is not None:
                    self._metrics.observe_fallback(backend=entry.backend_id, reason_code="unavailable")
                continue
            started = self._clock()
            try:
                result = entry.backend.audio_chat(filename=filename, content=content, context=context)
                self._record_success(entry.backend_id)
                if self._metrics is not None:
                    self._metrics.observe_backend_call(
                        operation="audio_chat",
                        backend=entry.backend_id,
                        outcome="succeeded",
                        duration_seconds=self._clock() - started,
                    )
                return result
            except Exception as exc:
                normalized = normalize_backend_exception(exc)
                last_exc = normalized
                self._record_failure(entry.backend_id)
                if self._metrics is not None:
                    self._metrics.observe_backend_call(
                        operation="audio_chat",
                        backend=entry.backend_id,
                        outcome=normalized.code,
                        duration_seconds=self._clock() - started,
                    )
                    self._metrics.observe_fallback(backend=entry.backend_id, reason_code=normalized.code)
                if normalized.code not in FALLBACK_ERROR_CODES:
                    raise normalized from exc
        if last_exc:
            raise last_exc
        raise RuntimeError("voice backend routing failed")

    def _circuit_may_recover(self, backend_id: str) -> bool:
        with self._circuit_lock:
            state = self._circuits[backend_id]
            return state.opened_at is None or self._clock() - state.opened_at >= self._cooldown_seconds

    def _acquire_circuit_attempt(self, backend_id: str) -> bool:
        """Allow all closed calls and exactly one half-open recovery probe."""

        with self._circuit_lock:
            state = self._circuits[backend_id]
            if state.opened_at is None:
                return True
            if self._clock() - state.opened_at < self._cooldown_seconds:
                if self._metrics is not None:
                    self._metrics.observe_circuit_event(backend=backend_id, event="open_skip")
                return False
            if state.half_open_inflight:
                if self._metrics is not None:
                    self._metrics.observe_circuit_event(backend=backend_id, event="open_skip")
                return False
            state.half_open_inflight = True
            if self._metrics is not None:
                self._metrics.observe_circuit_event(backend=backend_id, event="half_open_probe")
            return True

    def _record_success(self, backend_id: str) -> None:
        with self._circuit_lock:
            state = self._circuits[backend_id]
            recovered = state.opened_at is not None or state.half_open_inflight
            state.failures = 0
            state.opened_at = None
            state.half_open_inflight = False
            if recovered and self._metrics is not None:
                self._metrics.observe_circuit_event(backend=backend_id, event="recovered")

    def _record_failure(self, backend_id: str) -> None:
        with self._circuit_lock:
            state = self._circuits[backend_id]
            was_half_open = state.half_open_inflight
            state.failures += 1
            state.half_open_inflight = False
            if state.failures >= self._failure_threshold:
                opened = state.opened_at is None or was_half_open
                state.opened_at = self._clock()
                if opened and self._metrics is not None:
                    self._metrics.observe_circuit_event(backend=backend_id, event="opened")


class RoutedVoiceBackendResolver:
    """Long-lived catalog of lazy backends exposed only through routed views."""

    def __init__(
        self,
        *,
        config: VoiceRuntimeConfig,
        backend_ids: tuple[str, ...],
        model_catalog: VoiceModelCatalog | None = None,
        metrics: VoiceRuntimeMetricsPort | None = None,
        registry: VoiceBackendFactoryRegistry | None = None,
    ) -> None:
        factory_registry = registry or build_default_voice_backend_registry()
        normalized_ids = tuple(
            dict.fromkeys(str(backend_id or "").strip().lower() for backend_id in backend_ids)
        )
        if not normalized_ids or any(not backend_id for backend_id in normalized_ids):
            raise ValueError("voice backend catalog requires valid backend identifiers")
        entries: dict[str, _BackendEntry] = {}
        for backend_id in normalized_ids:
            try:
                backend = factory_registry.create_lazy(
                    backend_id,
                    config=config,
                    model_catalog=model_catalog,
                )
            except KeyError as exc:
                raise ValueError(f"unsupported ASR backend: {backend_id}") from exc
            entries[backend_id] = _BackendEntry(backend_id=backend_id, backend=backend)
        self._entries = entries
        self._metrics = metrics
        self._circuits = _SharedCircuitRegistry()
        self._circuits.register(tuple(entries))
        self._routes: dict[tuple[str, ...], RoutedVoiceBackend] = {}
        self._route_lock = threading.RLock()

    def resolve(self, backend_id: str) -> VoiceBackend:
        """Return the stable, circuit-managed route for one backend."""

        normalized = str(backend_id or "").strip().lower()
        if not normalized or normalized not in self._entries:
            raise ValueError(f"unsupported ASR backend: {normalized or '<empty>'}")
        return self.route((normalized,))

    def route(self, backend_ids: tuple[str, ...]) -> RoutedVoiceBackend:
        """Return a cached fallback route while retaining shared backend state."""

        normalized_ids = tuple(
            dict.fromkeys(str(backend_id or "").strip().lower() for backend_id in backend_ids)
        )
        if not normalized_ids or any(backend_id not in self._entries for backend_id in normalized_ids):
            unknown = next(
                (backend_id or "<empty>" for backend_id in normalized_ids if backend_id not in self._entries),
                "<empty>",
            )
            raise ValueError(f"unsupported ASR backend: {unknown}")
        with self._route_lock:
            route = self._routes.get(normalized_ids)
            if route is None:
                route = RoutedVoiceBackend(
                    [self._entries[backend_id] for backend_id in normalized_ids],
                    metrics=self._metrics,
                    circuit_registry=self._circuits,
                )
                self._routes[normalized_ids] = route
            return route

    def available_backends(self, backend_ids: tuple[str, ...] | None = None) -> dict[str, VoiceBackend]:
        """Expose native adapters only when their shared circuit permits a probe."""

        requested = backend_ids or tuple(self._entries)
        available: dict[str, VoiceBackend] = {}
        for backend_id in requested:
            normalized = str(backend_id or "").strip().lower()
            entry = self._entries.get(normalized)
            if entry is not None and self.route((normalized,))._circuit_may_recover(normalized):
                available[normalized] = entry.backend
        return available


def build_voice_backend_resolver(
    config: VoiceRuntimeConfig,
    *,
    backend_ids: tuple[str, ...] | None = None,
    model_catalog: VoiceModelCatalog | None = None,
    metrics: VoiceRuntimeMetricsPort | None = None,
    registry: VoiceBackendFactoryRegistry | None = None,
) -> RoutedVoiceBackendResolver:
    """Build the process-lifetime backend catalog for all configured policy paths."""

    selected_ids = backend_ids or _configured_backend_ids(config)
    return RoutedVoiceBackendResolver(
        config=config,
        backend_ids=selected_ids,
        model_catalog=model_catalog,
        metrics=metrics,
        registry=registry,
    )


def _configured_backend_ids(config: VoiceRuntimeConfig) -> tuple[str, ...]:
    selected = (
        *config.backend_fallback_order,
        config.primary_backend,
        *config.secondary_backends,
        config.asr_backend,
        config.rerun_backend,
        *config.policy_allowed_backends,
        *(("whisper_cpp",) if config.transcription_pipeline == "whisper_cpp" else ()),
    )
    return tuple(dict.fromkeys(selected))


def build_voice_backend_router(
    config: VoiceRuntimeConfig,
    *,
    model_catalog: VoiceModelCatalog | None = None,
    metrics: VoiceRuntimeMetricsPort | None = None,
    registry: VoiceBackendFactoryRegistry | None = None,
) -> RoutedVoiceBackend:
    resolver = build_voice_backend_resolver(
        config,
        backend_ids=config.backend_fallback_order,
        model_catalog=model_catalog,
        metrics=metrics,
        registry=registry,
    )
    return resolver.route(config.backend_fallback_order)
