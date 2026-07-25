from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from worker.retrieval.qdrant_vector_store import emit_operation_observation
from worker.retrieval.vector_store_contract import (
    VectorSearchPort,
    VectorSearchQuery,
    VectorSearchResult,
)
from worker.retrieval.vector_store_config import AvailabilityPolicy


@dataclass(frozen=True, slots=True)
class AvailabilityState:
    status: str
    reason: str


class AvailabilityProbe(Protocol):
    def probe(self) -> AvailabilityState: ...


class ClientAvailabilityProbe:
    def __init__(self, client: Any):
        self._client = client

    def probe(self) -> AvailabilityState:
        result = self._client.probe()
        return AvailabilityState(status=str(result.status), reason=str(result.reason))


class FallbackVectorSearch:
    _FALLBACK_REASONS = frozenset({"qdrant_unavailable", "qdrant_timeout"})

    def __init__(
        self,
        *,
        primary: VectorSearchPort,
        fallback: VectorSearchPort | None,
        policy: AvailabilityPolicy,
        availability_probe: AvailabilityProbe | None = None,
        fallback_compatibility: Callable[[VectorSearchQuery], bool] | None = None,
        observer: Any = None,
    ):
        self._primary = primary
        self._fallback = fallback
        self._policy = policy
        self._availability_probe = availability_probe
        self._fallback_compatibility = fallback_compatibility or (lambda _query: False)
        self._observer = observer

    def search_by_vector(self, query: VectorSearchQuery) -> VectorSearchResult:
        started = time.monotonic()
        availability = self._availability_probe.probe() if self._availability_probe else None
        if availability is None or availability.status == "ready":
            primary = self._primary.search_by_vector(query)
        else:
            primary = VectorSearchResult(
                hits=(),
                diagnostics={
                    "status": "degraded",
                    "reason": availability.reason,
                },
                requested_provider="qdrant",
                effective_provider="qdrant",
                provider_fallback=False,
                reason=availability.reason,
            )
        reason = str(primary.reason)
        if reason not in self._FALLBACK_REASONS:
            return primary
        mode = str(
            getattr(self._policy.on_unavailable, "value", self._policy.on_unavailable)
        )
        if mode == "fail_fast":
            result = primary
        elif mode == "degraded_empty":
            result = VectorSearchResult(
                hits=(),
                diagnostics={
                    **dict(primary.diagnostics),
                    "status": "degraded",
                    "reason": reason,
                },
                requested_provider="qdrant",
                effective_provider="qdrant",
                provider_fallback=False,
                reason=reason,
            )
        elif (
            mode == "explicit_json_fallback"
            and self._fallback is not None
            and str(
                getattr(
                    self._policy.fallback_provider,
                    "value",
                    self._policy.fallback_provider or "",
                )
            )
            == "json"
        ):
            if not self._fallback_compatibility(query):
                result = VectorSearchResult(
                    hits=(),
                    diagnostics={"status": "degraded", "reason": "fallback_state_incompatible"},
                    requested_provider="qdrant",
                    effective_provider="qdrant",
                    provider_fallback=False,
                    reason="fallback_state_incompatible",
                )
            else:
                fallback = self._fallback.search_by_vector(query)
                result = VectorSearchResult(
                    hits=fallback.hits,
                    diagnostics={
                        **dict(fallback.diagnostics),
                        "provider_fallback": True,
                        "requested_backend": "qdrant",
                        "effective_backend": "json",
                        "reason": reason,
                    },
                    requested_provider="qdrant",
                    effective_provider="json",
                    provider_fallback=True,
                    reason=reason,
                )
        else:
            result = VectorSearchResult(
                hits=(),
                diagnostics={"status": "degraded", "reason": "fallback_not_configured"},
                requested_provider="qdrant",
                effective_provider="qdrant",
                provider_fallback=False,
                reason="fallback_not_configured",
            )
        emit_operation_observation(
            self._observer,
            operation="search",
            outcome="degraded" if not result.provider_fallback else "success",
            reason=result.reason,
            duration_seconds=time.monotonic() - started,
            counts={"hits": len(result.hits)},
            requested_backend="qdrant",
            effective_backend=result.effective_provider,
            provider_fallback=result.provider_fallback,
        )
        return result
