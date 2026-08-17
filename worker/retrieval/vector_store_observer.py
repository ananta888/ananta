"""Backend-neutral observations emitted by vector-store operations.

The contract deliberately permits only bounded categorical values and numeric
counts. Scope identifiers, collection names, paths, profiles, payloads and
vectors have no representation here.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping, Protocol

VECTOR_STORE_BACKENDS = frozenset({"json", "qdrant", "duckdb"})
VECTOR_STORE_OPERATIONS = frozenset(
    {
        "health",
        "diagnostics",
        "search",
        "index",
        "rebuild",
        "refresh",
        "upsert",
        "delete",
        "migrate",
        "close",
    }
)
VECTOR_STORE_OUTCOMES = frozenset({"success", "degraded", "failed", "skipped"})
VECTOR_STORE_COUNT_KEYS = frozenset(
    {
        "accepted",
        "deleted",
        "embedded",
        "failed",
        "hits",
        "skipped",
        "top_k",
        "upserted",
    }
)
VECTOR_STORE_REASON_CODES = frozenset(
    {
        "ok",
        "deleted",
        "empty_collection",
        "empty_scope",
        "fallback_not_configured",
        "migrated",
        "migration_checkpoint_invalid",
        "migration_paused",
        "migration_ready",
        "missing_source_hash",
        "point_count_mismatch",
        "qdrant_timeout",
        "qdrant_unauthorized",
        "qdrant_unavailable",
        "rebuild",
        "refreshed",
        "unchanged",
        "upserted",
        "unavailable",
        "unauthorized",
        "incompatible_collection",
        "timeout",
        "dimensions_mismatch",
        "distance_mismatch",
        "provider_changed",
        "model_changed",
        "profile_changed",
        "encoding_changed",
        "config_changed",
        "manifest_changed",
        "vector_batch_size_invalid",
        "vector_scope_conflict",
        "vector_scope_required",
        "vector_store_compatibility_required",
        "vector_top_k_invalid",
        "vector_payload_invalid",
        "vector_payload_too_large",
        "vector_point_id_mismatch",
        "fallback_state_incompatible",
        "qdrant_extra_required",
        "collection_missing",
        "provider_fallback",
        "migration_required",
        "rebuild_required",
        "closed",
        "other",
    }
)


def bounded_vector_store_reason(value: str | None) -> str:
    candidate = str(value or "other").strip().lower()
    return candidate if candidate in VECTOR_STORE_REASON_CODES else "other"


def observation_outcome(status: str) -> str:
    return {
        "ok": "success",
        "success": "success",
        "partial": "degraded",
        "degraded": "degraded",
        "failed": "failed",
        "skipped": "skipped",
    }.get(str(status or "").lower(), "failed")


@dataclass(frozen=True)
class VectorStoreOperationObservation:
    backend: str
    operation: str
    outcome: str
    reason_code: str = "ok"
    duration_seconds: float = 0.0
    counts: Mapping[str, int] = field(default_factory=dict)
    requested_backend: str | None = None
    effective_backend: str | None = None
    provider_fallback: bool = False

    def __post_init__(self) -> None:
        backend = str(self.backend or "").strip().lower()
        operation = str(self.operation or "").strip().lower()
        outcome = str(self.outcome or "").strip().lower()
        if backend not in VECTOR_STORE_BACKENDS:
            raise ValueError("vector_store_observation_backend_invalid")
        if operation not in VECTOR_STORE_OPERATIONS:
            raise ValueError("vector_store_observation_operation_invalid")
        if outcome not in VECTOR_STORE_OUTCOMES:
            raise ValueError("vector_store_observation_outcome_invalid")
        duration = float(self.duration_seconds)
        if not math.isfinite(duration) or duration < 0:
            raise ValueError("vector_store_observation_duration_invalid")
        normalized_counts: dict[str, int] = {}
        for raw_key, raw_value in dict(self.counts or {}).items():
            key = str(raw_key or "").strip().lower()
            if key not in VECTOR_STORE_COUNT_KEYS:
                raise ValueError("vector_store_observation_count_key_invalid")
            value = int(raw_value)
            if value < 0:
                raise ValueError("vector_store_observation_count_invalid")
            normalized_counts[key] = value
        requested = (
            str(self.requested_backend or "").strip().lower()
            if self.requested_backend is not None
            else None
        )
        effective = (
            str(self.effective_backend or "").strip().lower()
            if self.effective_backend is not None
            else None
        )
        if requested is not None and requested not in VECTOR_STORE_BACKENDS:
            raise ValueError("vector_store_observation_requested_backend_invalid")
        if effective is not None and effective not in VECTOR_STORE_BACKENDS:
            raise ValueError("vector_store_observation_effective_backend_invalid")
        if self.provider_fallback and (requested is None or effective is None):
            raise ValueError("vector_store_observation_fallback_backends_required")
        object.__setattr__(self, "backend", backend)
        object.__setattr__(self, "operation", operation)
        object.__setattr__(self, "outcome", outcome)
        object.__setattr__(self, "reason_code", bounded_vector_store_reason(self.reason_code))
        object.__setattr__(self, "duration_seconds", duration)
        object.__setattr__(self, "counts", MappingProxyType(normalized_counts))
        object.__setattr__(self, "requested_backend", requested)
        object.__setattr__(self, "effective_backend", effective)


class VectorStoreObserver(Protocol):
    def observe(self, observation: VectorStoreOperationObservation) -> None: ...


class NullVectorStoreObserver:
    def observe(self, observation: VectorStoreOperationObservation) -> None:
        del observation


def emit_operation_observation(
    observer: Any,
    *,
    backend: str = "qdrant",
    operation: str,
    outcome: str,
    reason: str,
    duration_seconds: float,
    counts: Mapping[str, int] | None = None,
    requested_backend: str | None = None,
    effective_backend: str | None = None,
    provider_fallback: bool = False,
) -> None:
    """Emit a bounded observation without changing vector-store behavior."""

    if observer is None:
        return
    try:
        observation = VectorStoreOperationObservation(
            backend=backend,
            operation=operation,
            outcome=outcome,
            reason_code=reason,
            duration_seconds=max(0.0, float(duration_seconds)),
            counts=dict(counts or {}),
            requested_backend=requested_backend,
            effective_backend=effective_backend,
            provider_fallback=provider_fallback,
        )
        observer.observe(observation)
    except Exception:
        # Observability is optional and must never alter retrieval behavior.
        return


__all__ = [
    "NullVectorStoreObserver",
    "VECTOR_STORE_BACKENDS",
    "VECTOR_STORE_COUNT_KEYS",
    "VECTOR_STORE_OPERATIONS",
    "VECTOR_STORE_OUTCOMES",
    "VECTOR_STORE_REASON_CODES",
    "VectorStoreObserver",
    "VectorStoreOperationObservation",
    "bounded_vector_store_reason",
    "emit_operation_observation",
    "observation_outcome",
]
