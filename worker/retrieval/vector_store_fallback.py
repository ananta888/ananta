from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, NoReturn, Protocol, Sequence

from worker.retrieval.vector_store_config import AvailabilityPolicy
from worker.retrieval.vector_store_contract import (
    CompatibilitySpec,
    IndexWriteResult,
    PlannedVectorIndexWriter,
    PreparedVectorPoint,
    VectorIndexWritePlan,
    VectorScope,
    VectorSearchPort,
    VectorSearchQuery,
    VectorSearchResult,
    VectorStore,
    VectorStoreDiagnostic,
    VectorStoreError,
    VectorStoreFailClosedError,
)
from worker.retrieval.vector_store_observer import emit_operation_observation


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
        reason = str(result.reason)
        status = str(result.status)
        if reason == "qdrant_unauthorized":
            status = "unauthorized"
        elif reason in {
            "incompatible_collection",
            "vector_store_compatibility_required",
        }:
            status = "incompatible_collection"
        return AvailabilityState(status=status, reason=reason)


class FallbackVectorSearch:
    _FALLBACK_REASONS = frozenset({"qdrant_unavailable", "qdrant_timeout"})
    _FAIL_CLOSED_REASONS = frozenset(
        {
            "qdrant_unauthorized",
            "incompatible_collection",
            "vector_store_compatibility_required",
        }
    )
    _FAIL_CLOSED_STATUS_REASONS = {
        "unauthorized": "qdrant_unauthorized",
        "incompatible_collection": "incompatible_collection",
    }

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

    @classmethod
    def _fail_closed_reason(
        cls,
        *,
        status: str | None = None,
        reason: str | None = None,
    ) -> str | None:
        normalized_reason = str(reason or "").strip().lower()
        if normalized_reason in cls._FAIL_CLOSED_REASONS:
            return normalized_reason
        return cls._FAIL_CLOSED_STATUS_REASONS.get(
            str(status or "").strip().lower()
        )

    def _raise_fail_closed(
        self,
        *,
        query: VectorSearchQuery,
        started: float,
        reason: str,
        status: str | None = None,
    ) -> NoReturn:
        normalized_status = str(status or "").strip().lower()
        emit_operation_observation(
            self._observer,
            operation="search",
            outcome="failed",
            reason=reason,
            duration_seconds=time.monotonic() - started,
            counts={"top_k": query.top_k, "hits": 0},
            requested_backend="qdrant",
            effective_backend="qdrant",
            provider_fallback=False,
        )
        details: dict[str, Any] = {
            "requested_backend": "qdrant",
            "effective_backend": "qdrant",
            "provider_fallback": False,
        }
        if normalized_status in self._FAIL_CLOSED_STATUS_REASONS:
            details["availability_status"] = normalized_status
        raise VectorStoreFailClosedError(reason, details=details)

    def search_by_vector(self, query: VectorSearchQuery) -> VectorSearchResult:
        started = time.monotonic()
        availability = self._availability_probe.probe() if self._availability_probe else None
        if availability is None or availability.status == "ready":
            try:
                primary = self._primary.search_by_vector(query)
            except VectorStoreError as exc:
                fail_closed_reason = self._fail_closed_reason(reason=exc.reason)
                if fail_closed_reason is not None:
                    self._raise_fail_closed(
                        query=query,
                        started=started,
                        reason=fail_closed_reason,
                    )
                if exc.reason not in self._FALLBACK_REASONS:
                    raise
                primary = VectorSearchResult(
                    hits=(),
                    diagnostics={"status": "degraded", "reason": exc.reason},
                    requested_provider="qdrant",
                    effective_provider="qdrant",
                    provider_fallback=False,
                    reason=exc.reason,
                )
        else:
            fail_closed_reason = self._fail_closed_reason(
                status=availability.status,
                reason=availability.reason,
            )
            if fail_closed_reason is not None:
                self._raise_fail_closed(
                    query=query,
                    started=started,
                    reason=fail_closed_reason,
                    status=availability.status,
                )
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
        fail_closed_reason = self._fail_closed_reason(
            status=str(primary.diagnostics.get("status") or ""),
            reason=reason,
        )
        if fail_closed_reason is not None:
            self._raise_fail_closed(
                query=query,
                started=started,
                reason=fail_closed_reason,
                status=str(primary.diagnostics.get("status") or ""),
            )
        if reason not in self._FALLBACK_REASONS:
            return primary
        mode = str(
            getattr(self._policy.on_unavailable, "value", self._policy.on_unavailable)
        )
        fail_fast = False
        if mode == "fail_fast":
            result = primary
            fail_fast = True
        elif mode == "degraded_empty":
            result = VectorSearchResult(
                hits=(),
                diagnostics={
                    **dict(primary.diagnostics),
                    "status": "degraded",
                    "reason": reason,
                    "provider_fallback": False,
                    "requested_backend": "qdrant",
                    "effective_backend": "qdrant",
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
                    diagnostics={
                        "status": "degraded",
                        "reason": "fallback_state_incompatible",
                        "provider_fallback": False,
                        "requested_backend": "qdrant",
                        "effective_backend": "qdrant",
                    },
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
                diagnostics={
                    "status": "degraded",
                    "reason": "fallback_not_configured",
                    "provider_fallback": False,
                    "requested_backend": "qdrant",
                    "effective_backend": "qdrant",
                },
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
            counts={"top_k": query.top_k, "hits": len(result.hits)},
            requested_backend="qdrant",
            effective_backend=result.effective_provider,
            provider_fallback=result.provider_fallback,
        )
        if fail_fast:
            raise VectorStoreError(
                reason,
                details={
                    "requested_backend": "qdrant",
                    "effective_backend": "qdrant",
                    "provider_fallback": False,
                },
            )
        return result


class AvailabilityManagedVectorStore:
    """Full store decorator whose availability decision is limited to read-only search."""

    def __init__(
        self,
        *,
        primary: VectorStore,
        search: FallbackVectorSearch,
        fallback: VectorStore | None = None,
    ) -> None:
        self._primary = primary
        self._search = search
        self._fallback = fallback

    @property
    def primary(self) -> VectorStore:
        return self._primary

    def search_by_vector(self, query: VectorSearchQuery) -> VectorSearchResult:
        return self._search.search_by_vector(query)

    def rebuild(
        self,
        points: Sequence[PreparedVectorPoint],
        *,
        compatibility: CompatibilitySpec,
    ) -> IndexWriteResult:
        return self._primary.rebuild(points, compatibility=compatibility)

    def refresh(
        self,
        points: Sequence[PreparedVectorPoint],
        *,
        compatibility: CompatibilitySpec,
    ) -> IndexWriteResult:
        return self._primary.refresh(points, compatibility=compatibility)

    def rebuild_with_plan(
        self,
        points: Sequence[PreparedVectorPoint],
        *,
        compatibility: CompatibilitySpec,
        plan: VectorIndexWritePlan,
    ) -> IndexWriteResult:
        if isinstance(self._primary, PlannedVectorIndexWriter):
            return self._primary.rebuild_with_plan(
                points,
                compatibility=compatibility,
                plan=plan,
            )
        return self._primary.rebuild(points, compatibility=compatibility)

    def refresh_with_plan(
        self,
        points: Sequence[PreparedVectorPoint],
        *,
        compatibility: CompatibilitySpec,
        plan: VectorIndexWritePlan,
    ) -> IndexWriteResult:
        if isinstance(self._primary, PlannedVectorIndexWriter):
            return self._primary.refresh_with_plan(
                points,
                compatibility=compatibility,
                plan=plan,
            )
        return self._primary.refresh(points, compatibility=compatibility)

    def upsert(
        self,
        points: Sequence[PreparedVectorPoint],
        *,
        batch_size: int = 128,
    ) -> IndexWriteResult:
        return self._primary.upsert(points, batch_size=batch_size)

    def delete(
        self,
        point_ids: Sequence[str],
        *,
        scope: VectorScope,
    ) -> IndexWriteResult:
        return self._primary.delete(point_ids, scope=scope)

    def delete_scope(self, scope: VectorScope) -> IndexWriteResult:
        return self._primary.delete_scope(scope)

    def diagnostics(self) -> VectorStoreDiagnostic:
        return self._primary.diagnostics()

    def close(self) -> None:
        primary_error: Exception | None = None
        try:
            self._primary.close()
        except Exception as exc:  # pragma: no cover - defensive lifecycle cleanup
            primary_error = exc
        if self._fallback is not None and self._fallback is not self._primary:
            self._fallback.close()
        if primary_error is not None:
            raise primary_error


__all__ = [
    "AvailabilityManagedVectorStore",
    "AvailabilityProbe",
    "AvailabilityState",
    "ClientAvailabilityProbe",
    "FallbackVectorSearch",
]
