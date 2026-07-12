"""Content-free, bounded-cardinality Hub metrics for Voice operations."""

from __future__ import annotations

from typing import Any, Mapping, cast

from agent.metrics import (
    VOICE_AUDIO_DURATION_SECONDS,
    VOICE_BACKPRESSURE_TOTAL,
    VOICE_FALLBACK_TOTAL,
    VOICE_HUB_DURATION_SECONDS,
    VOICE_HUB_REQUESTS_TOTAL,
    VOICE_REAL_TIME_FACTOR,
    VOICE_RERUN_TOTAL,
    VOICE_STREAM_EVENTS_TOTAL,
)

_OPERATIONS = frozenset({"capabilities", "transcribe", "command", "goal", "stream"})
_BACKENDS = frozenset({"vosk", "whisper_cpp", "faster_whisper", "voxtral", "mock"})
_ERROR_CODES = frozenset(
    {
        "ok",
        "invalid_input",
        "policy_blocked",
        "unavailable",
        "timeout",
        "cancelled",
        "model_error",
        "resource_exhausted",
        "backpressure",
    }
)
_STREAM_EVENTS = frozenset(
    {"created", "ack", "chunk_accepted", "chunk_replayed", "partial", "final", "cancelled", "error"}
)


def record_voice_request(*, operation: str, outcome: str, error_code: str, duration_seconds: float) -> None:
    safe_operation = operation if operation in _OPERATIONS else "other"
    safe_outcome = outcome if outcome in {"succeeded", "failed", "blocked"} else "failed"
    safe_error = _bounded_error(error_code)
    VOICE_HUB_REQUESTS_TOTAL.labels(safe_operation, safe_outcome, safe_error).inc()
    VOICE_HUB_DURATION_SECONDS.labels(safe_operation, safe_outcome).observe(max(0.0, float(duration_seconds)))
    if safe_error == "backpressure":
        VOICE_BACKPRESSURE_TOTAL.labels("hub").inc()


def record_voice_result(result: Mapping[str, Any]) -> None:
    backend = _bounded_backend(result.get("raw_backend"))
    duration_ms = _finite_non_negative(result.get("duration_ms"))
    if duration_ms is not None:
        VOICE_AUDIO_DURATION_SECONDS.labels(backend).observe(duration_ms / 1000.0)
    candidates_value = result.get("candidates")
    candidates: list[Any] = candidates_value if isinstance(candidates_value, list) else []
    for candidate in candidates[:16]:
        if not isinstance(candidate, Mapping):
            continue
        real_time_factor = _finite_non_negative(candidate.get("real_time_factor"))
        if real_time_factor is not None:
            VOICE_REAL_TIME_FACTOR.labels(_bounded_backend(candidate.get("backend"))).observe(real_time_factor)
    trace_value = result.get("decision_trace")
    trace: Mapping[str, Any] = trace_value if isinstance(trace_value, Mapping) else {}
    attempts_value = trace.get("fallback_attempts")
    attempts: list[Any] = attempts_value if isinstance(attempts_value, list) else []
    for attempt in attempts[:8]:
        if isinstance(attempt, Mapping) and attempt.get("status") in {"failed", "skipped"}:
            VOICE_FALLBACK_TOTAL.labels(
                _bounded_backend(attempt.get("backend")),
                _bounded_error(attempt.get("reason_code")),
            ).inc()
    rerun_backend = result.get("rerun_backend")
    if rerun_backend:
        outcome = "failed" if "confidence_rerun_failed" in set(result.get("warnings") or []) else "succeeded"
        VOICE_RERUN_TOTAL.labels(_bounded_backend(rerun_backend), outcome).inc()


def record_stream_event(event_type: object, *, outcome: str = "succeeded", error_code: object = "ok") -> None:
    normalized = str(event_type or "error").strip().lower()
    safe_event = normalized if normalized in _STREAM_EVENTS else "other"
    safe_outcome = outcome if outcome in {"succeeded", "failed", "blocked"} else "failed"
    VOICE_STREAM_EVENTS_TOTAL.labels(safe_event, safe_outcome).inc()
    if _bounded_error(error_code) == "backpressure":
        VOICE_BACKPRESSURE_TOTAL.labels("stream").inc()


def _bounded_backend(value: object) -> str:
    normalized = str(value or "unknown").strip().lower()
    return normalized if normalized in _BACKENDS else "other"


def _bounded_error(value: object) -> str:
    normalized = str(value or "model_error").strip().lower().removeprefix("voice.")
    if normalized.startswith("stream.backpressure"):
        return "backpressure"
    return normalized if normalized in _ERROR_CODES else "other"


def _finite_non_negative(value: object) -> float | None:
    try:
        number = float(cast(Any, value))
    except (TypeError, ValueError):
        return None
    if number < 0 or number == float("inf") or number != number:
        return None
    return number
