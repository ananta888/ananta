"""Bounded-cardinality observability for the isolated Voice Runtime.

The runtime deliberately owns its own CollectorRegistry.  This keeps the
worker image independent from the Hub's process-global metrics and makes app
factory instances deterministic in tests.  Only fixed enumerations are ever
used as labels; request IDs, model IDs, paths, filenames and recognized text
must remain out of Prometheus.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Protocol

from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, Gauge, Histogram, generate_latest

if TYPE_CHECKING:
    from .backends.base import TranscriptionResult

_BACKENDS = frozenset({"vosk", "whisper_cpp", "faster_whisper", "voxtral", "mock", "fusion"})
_BACKEND_OPERATIONS = frozenset({"transcribe", "audio_chat", "candidate"})
_BACKEND_OUTCOMES = frozenset(
    {
        "succeeded",
        "invalid_input",
        "policy_blocked",
        "unavailable",
        "timeout",
        "cancelled",
        "model_error",
        "resource_exhausted",
        "other",
    }
)
_CIRCUIT_EVENTS = frozenset({"opened", "open_skip", "half_open_probe", "recovered"})
_FUSION_STRATEGIES = frozenset({"deterministic_consensus"})
_HTTP_OPERATIONS_BY_ENDPOINT = {
    "voice_runtime.health": "health",
    "voice_runtime.models": "models",
    "voice_runtime.transcriptions": "transcribe",
    "voice_runtime.audio_chat": "audio_chat",
    "voice_runtime.create_stream": "stream_create",
    "voice_runtime.stream_chunk": "stream_chunk",
    "voice_runtime.finalize_stream": "stream_finalize",
    "voice_runtime.get_stream": "stream_get",
    "voice_runtime.delete_stream": "stream_delete",
    "voice_runtime.metrics": "metrics",
}
_QUEUE_SURFACES = frozenset({"candidate_dispatch"})
_STREAM_EVENTS = frozenset(
    {
        "created",
        "chunk_accepted",
        "chunk_replayed",
        "partial",
        "final",
        "final_replayed",
        "closed",
        "error",
    }
)
_STREAM_OUTCOMES = frozenset(
    {
        "succeeded",
        "backpressure",
        "conflict",
        "invalid_input",
        "timeout",
        "resource_exhausted",
        "server_error",
        "other",
    }
)


class VoiceRuntimeMetricsPort(Protocol):
    """Small execution-side metrics port used by backend adapters."""

    def observe_backend_call(
        self,
        *,
        operation: str,
        backend: object,
        outcome: object,
        duration_seconds: float,
    ) -> None: ...

    def observe_circuit_event(self, *, backend: object, event: str) -> None: ...

    def observe_fallback(self, *, backend: object, reason_code: object) -> None: ...

    def observe_queue_wait(self, *, surface: str, outcome: str, duration_seconds: float) -> None: ...


class VoiceRuntimeMetrics(VoiceRuntimeMetricsPort):
    """Prometheus collector facade with fixed, privacy-safe dimensions."""

    content_type = CONTENT_TYPE_LATEST

    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        self.registry = registry or CollectorRegistry(auto_describe=True)
        self.requests = Counter(
            "voice_runtime_requests_total",
            "Voice Runtime HTTP requests grouped by bounded operation and outcome",
            ("operation", "outcome"),
            registry=self.registry,
        )
        self.request_duration = Histogram(
            "voice_runtime_request_duration_seconds",
            "Voice Runtime HTTP request duration",
            ("operation", "outcome"),
            buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 15, 30, 60, 120),
            registry=self.registry,
        )
        self.errors = Counter(
            "voice_runtime_errors_total",
            "Voice Runtime failures grouped by bounded surface and reason",
            ("surface", "reason_code"),
            registry=self.registry,
        )
        self.backend_calls = Counter(
            "voice_runtime_backend_calls_total",
            "Local backend calls grouped by operation, bounded backend and outcome",
            ("operation", "backend", "outcome"),
            registry=self.registry,
        )
        self.backend_duration = Histogram(
            "voice_runtime_backend_duration_seconds",
            "Local backend call duration",
            ("operation", "backend", "outcome"),
            buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 15, 30, 60, 120),
            registry=self.registry,
        )
        self.queue_wait = Histogram(
            "voice_runtime_queue_wait_seconds",
            "Time spent acquiring bounded local execution capacity",
            ("surface", "outcome"),
            buckets=(0.0, 0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1),
            registry=self.registry,
        )
        self.audio_duration = Histogram(
            "voice_runtime_audio_duration_seconds",
            "Recognized audio duration grouped by bounded backend",
            ("backend",),
            buckets=(0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60, 120, 300, 900, 1800, 3600),
            registry=self.registry,
        )
        self.real_time_factor = Histogram(
            "voice_runtime_real_time_factor",
            "Candidate real-time factor grouped by bounded backend",
            ("backend",),
            buckets=(0.05, 0.1, 0.25, 0.5, 0.75, 1, 1.5, 2, 3, 5, 10),
            registry=self.registry,
        )
        self.candidates = Counter(
            "voice_runtime_candidates_total",
            "Completed local candidates grouped by bounded backend and outcome",
            ("backend", "outcome"),
            registry=self.registry,
        )
        self.fusions = Counter(
            "voice_runtime_fusions_total",
            "Fusion results grouped by bounded strategy and outcome",
            ("strategy", "outcome"),
            registry=self.registry,
        )
        self.fusion_candidate_count = Histogram(
            "voice_runtime_fusion_candidate_count",
            "Number of candidates presented to a fusion result",
            ("strategy",),
            buckets=(0, 1, 2, 3, 4, 6, 8, 12, 16),
            registry=self.registry,
        )
        self.fusion_disagreement_count = Histogram(
            "voice_runtime_fusion_disagreement_count",
            "Number of disagreement regions produced by fusion",
            ("strategy",),
            buckets=(0, 1, 2, 3, 5, 8, 13, 21, 34),
            registry=self.registry,
        )
        self.fallbacks = Counter(
            "voice_runtime_fallback_total",
            "Fallback decisions grouped by bounded backend and reason",
            ("backend", "reason_code"),
            registry=self.registry,
        )
        self.reruns = Counter(
            "voice_runtime_rerun_total",
            "Confidence reruns grouped by bounded backend and outcome",
            ("backend", "outcome"),
            registry=self.registry,
        )
        self.stream_events = Counter(
            "voice_runtime_stream_events_total",
            "Streaming protocol events grouped by bounded event and outcome",
            ("event_type", "outcome"),
            registry=self.registry,
        )
        self.stream_chunk_bytes = Histogram(
            "voice_runtime_stream_chunk_bytes",
            "Accepted streaming chunk sizes without session- or content-derived labels",
            buckets=(128, 512, 1024, 4096, 16384, 65536, 262144, 524288, 1048576),
            registry=self.registry,
        )
        self.backpressure = Counter(
            "voice_runtime_backpressure_total",
            "Fail-fast backpressure events grouped by bounded surface",
            ("surface",),
            registry=self.registry,
        )
        self.circuit_events = Counter(
            "voice_runtime_circuit_breaker_events_total",
            "Backend circuit-breaker transitions grouped by bounded backend and event",
            ("backend", "event"),
            registry=self.registry,
        )
        self.privacy_state = Gauge(
            "voice_runtime_privacy_state",
            "Current bounded privacy state (1 means the state tuple is active)",
            ("store_audio_requested", "store_audio_effective"),
            registry=self.registry,
        )

    def set_privacy_state(self, *, store_audio_requested: bool, store_audio_effective: bool) -> None:
        self.privacy_state.labels(
            _bool_label(store_audio_requested),
            _bool_label(store_audio_effective),
        ).set(1)

    def observe_http_request(self, *, endpoint: object, status_code: int, duration_seconds: float) -> None:
        operation = operation_for_endpoint(endpoint)
        if operation == "metrics":
            return
        outcome = _http_outcome(status_code)
        duration = _finite_non_negative(duration_seconds)
        self.requests.labels(operation, outcome).inc()
        self.request_duration.labels(operation, outcome).observe(duration or 0.0)
        if status_code >= 400:
            self.errors.labels("http", _status_reason(status_code)).inc()

    def observe_backend_call(
        self,
        *,
        operation: str,
        backend: object,
        outcome: object,
        duration_seconds: float,
    ) -> None:
        safe_operation = operation if operation in _BACKEND_OPERATIONS else "other"
        safe_backend = bounded_backend(backend)
        safe_outcome = bounded_backend_outcome(outcome)
        duration = _finite_non_negative(duration_seconds)
        self.backend_calls.labels(safe_operation, safe_backend, safe_outcome).inc()
        self.backend_duration.labels(safe_operation, safe_backend, safe_outcome).observe(duration or 0.0)
        if safe_outcome != "succeeded":
            self.errors.labels("backend", safe_outcome).inc()

    def observe_circuit_event(self, *, backend: object, event: str) -> None:
        safe_event = event if event in _CIRCUIT_EVENTS else "other"
        self.circuit_events.labels(bounded_backend(backend), safe_event).inc()

    def observe_fallback(self, *, backend: object, reason_code: object) -> None:
        self.fallbacks.labels(bounded_backend(backend), bounded_backend_outcome(reason_code)).inc()

    def observe_queue_wait(self, *, surface: str, outcome: str, duration_seconds: float) -> None:
        safe_surface = surface if surface in _QUEUE_SURFACES else "other"
        safe_outcome = outcome if outcome in {"acquired", "resource_exhausted"} else "other"
        duration = _finite_non_negative(duration_seconds)
        self.queue_wait.labels(safe_surface, safe_outcome).observe(duration or 0.0)
        if safe_outcome == "resource_exhausted":
            self.backpressure.labels(safe_surface).inc()

    def observe_transcription_result(self, result: "TranscriptionResult") -> None:
        backend = bounded_backend(result.raw_backend)
        duration_ms = _finite_non_negative(result.duration_ms)
        if duration_ms is not None:
            self.audio_duration.labels(backend).observe(duration_ms / 1000.0)

        for candidate in result.candidates[:16]:
            candidate_backend = bounded_backend(candidate.backend)
            candidate_outcome = "succeeded" if candidate.status == "succeeded" else "failed"
            self.candidates.labels(candidate_backend, candidate_outcome).inc()
            rtf = _finite_non_negative(candidate.real_time_factor)
            if rtf is not None:
                self.real_time_factor.labels(candidate_backend).observe(rtf)

        if result.fusion_strategy:
            strategy = bounded_fusion_strategy(result.fusion_strategy)
            if not result.candidates:
                outcome = "no_candidates"
            elif not result.provenance_valid:
                outcome = "invalid_provenance"
            else:
                outcome = "succeeded"
            self.fusions.labels(strategy, outcome).inc()
            self.fusion_candidate_count.labels(strategy).observe(min(16, len(result.candidates)))
            self.fusion_disagreement_count.labels(strategy).observe(min(34, len(result.disagreement_regions)))

        if result.rerun_backend:
            rerun_outcome = "failed" if "confidence_rerun_failed" in result.warnings else "succeeded"
            self.reruns.labels(bounded_backend(result.rerun_backend), rerun_outcome).inc()

    def observe_stream_event(
        self,
        event_type: object,
        *,
        outcome: object = "succeeded",
        accepted_bytes: object | None = None,
    ) -> None:
        normalized_event = str(event_type or "error").strip().lower()
        safe_event = normalized_event if normalized_event in _STREAM_EVENTS else "other"
        safe_outcome = bounded_stream_outcome(outcome)
        self.stream_events.labels(safe_event, safe_outcome).inc()
        chunk_bytes = _finite_non_negative(accepted_bytes)
        if safe_event in {"chunk_accepted", "partial"} and chunk_bytes is not None:
            self.stream_chunk_bytes.observe(min(chunk_bytes, 1_048_576))
        if safe_outcome == "backpressure":
            self.backpressure.labels("stream").inc()
        if safe_outcome != "succeeded":
            self.errors.labels("stream", safe_outcome).inc()

    def render(self) -> bytes:
        return bytes(generate_latest(self.registry))


def operation_for_endpoint(endpoint: object) -> str:
    return _HTTP_OPERATIONS_BY_ENDPOINT.get(str(endpoint or ""), "other")


def bounded_backend(value: object) -> str:
    normalized = str(value or "unknown").strip().lower()
    return normalized if normalized in _BACKENDS else "other"


def bounded_backend_outcome(value: object) -> str:
    normalized = str(value or "other").strip().lower().removeprefix("voice.")
    return normalized if normalized in _BACKEND_OUTCOMES else "other"


def bounded_fusion_strategy(value: object) -> str:
    normalized = str(value or "other").strip().lower()
    return normalized if normalized in _FUSION_STRATEGIES else "other"


def bounded_stream_outcome(value: object) -> str:
    normalized = str(value or "other").strip().lower().removeprefix("voice.")
    aliases = {
        "stream.backpressure": "backpressure",
        "stream.capacity_exhausted": "resource_exhausted",
        "stream.chunk_conflict": "conflict",
        "stream.sequence_gap": "conflict",
        "stream.deadline_exceeded": "timeout",
        "stream.empty": "invalid_input",
        "stream.empty_chunk": "invalid_input",
        "stream.chunk_too_large": "invalid_input",
        "stream.total_too_large": "invalid_input",
        "stream.unsupported_media_type": "invalid_input",
        "stream.invalid_state": "conflict",
    }
    normalized = aliases.get(normalized, normalized)
    return normalized if normalized in _STREAM_OUTCOMES else "other"


def _http_outcome(status_code: int) -> str:
    if 200 <= status_code < 400:
        return "succeeded"
    if status_code in {401, 403}:
        return "unauthorized"
    if status_code == 409:
        return "conflict"
    if status_code == 429:
        return "resource_exhausted"
    if status_code == 504:
        return "timeout"
    if 400 <= status_code < 500:
        return "client_error"
    return "server_error"


def _status_reason(status_code: int) -> str:
    outcome = _http_outcome(status_code)
    allowed = {"unauthorized", "conflict", "resource_exhausted", "timeout", "client_error", "server_error"}
    return outcome if outcome in allowed else "other"


def _finite_non_negative(value: object) -> float | None:
    if not isinstance(value, (str, int, float)):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number < 0 or not math.isfinite(number):
        return None
    return number


def _bool_label(value: bool) -> str:
    return "true" if value else "false"
