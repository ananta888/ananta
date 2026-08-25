"""Canonical, source-isolated model inventory with bounded stale caching."""

from __future__ import annotations

import hashlib
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from ananta_contracts.model_catalog import (
    ModelAvailability,
    ModelCapabilityClaim,
    ModelCatalogV2,
    ModelHealth,
    ModelInventoryDescriptor,
    ModelInventorySourceStatus,
    ModelMetadataEvidence,
    ModelSourceKind,
)


@dataclass(frozen=True, slots=True)
class ModelInventorySnapshot:
    models: tuple[ModelInventoryDescriptor, ...]
    degraded_reason_code: str | None = None


class ModelSourceAdapterPort(Protocol):
    source_id: str
    source_kind: ModelSourceKind
    cache_ttl_seconds: float
    stale_after_seconds: float

    def collect(self, *, force_refresh: bool = False) -> ModelInventorySnapshot: ...


@dataclass(slots=True)
class _CachedSource:
    snapshot: ModelInventorySnapshot
    last_attempt_monotonic: float
    last_success_monotonic: float
    last_attempt_at: str
    last_success_at: str
    last_refresh_was_forced: bool = False


class ModelInventoryService:
    """Aggregates adapters without allowing one source to fail the catalog."""

    def __init__(self, adapters: tuple[ModelSourceAdapterPort, ...]) -> None:
        ids = [adapter.source_id for adapter in adapters]
        if len(ids) != len(set(ids)):
            raise ValueError("model_inventory_source_duplicate")
        self._adapters = adapters
        self._cache: dict[str, _CachedSource] = {}
        self._locks = {
            adapter.source_id: threading.Lock() for adapter in adapters
        }
        self._revision_lock = threading.Lock()
        self._catalog_revision = 1
        self._catalog_fingerprint = ""

    def catalog(self, *, force_refresh: bool = False) -> ModelCatalogV2:
        descriptors: list[ModelInventoryDescriptor] = []
        statuses: list[ModelInventorySourceStatus] = []
        for adapter in self._adapters:
            snapshot, status = self._source_snapshot(
                adapter,
                force_refresh=force_refresh,
            )
            descriptors.extend(snapshot.models)
            statuses.append(status)
        merged = self._merge(descriptors)
        revision = self._revision(merged)
        return ModelCatalogV2(
            catalog_revision=revision,
            models=merged,
            sources=tuple(statuses),
            partial=any(
                item.status in {"degraded", "unavailable", "stale"}
                for item in statuses
            ),
        )

    def _source_snapshot(
        self,
        adapter: ModelSourceAdapterPort,
        *,
        force_refresh: bool,
    ) -> tuple[ModelInventorySnapshot, ModelInventorySourceStatus]:
        requested_at = time.monotonic()
        cached = self._cache.get(adapter.source_id)
        if (
            force_refresh
            and cached is not None
            and cached.last_refresh_was_forced
            and requested_at - cached.last_attempt_monotonic < 1.0
        ):
            return cached.snapshot, self._status(
                adapter,
                cached,
                status=(
                    "degraded"
                    if cached.snapshot.degraded_reason_code
                    else "healthy"
                ),
                from_cache=True,
                reason_code=cached.snapshot.degraded_reason_code,
            )
        if (
            not force_refresh
            and cached is not None
            and requested_at - cached.last_success_monotonic
            < adapter.cache_ttl_seconds
        ):
            return cached.snapshot, self._status(
                adapter,
                cached,
                status=(
                    "degraded"
                    if cached.snapshot.degraded_reason_code
                    else "healthy"
                ),
                from_cache=True,
                reason_code=cached.snapshot.degraded_reason_code,
            )

        with self._locks[adapter.source_id]:
            cached = self._cache.get(adapter.source_id)
            if (
                cached is not None
                and (
                    (
                        force_refresh
                        and cached.last_refresh_was_forced
                        and time.monotonic() - cached.last_attempt_monotonic < 1.0
                    )
                    or
                    cached.last_attempt_monotonic >= requested_at
                    or (
                        not force_refresh
                        and time.monotonic() - cached.last_success_monotonic
                        < adapter.cache_ttl_seconds
                    )
                )
            ):
                return cached.snapshot, self._status(
                    adapter,
                    cached,
                    status=(
                        "degraded"
                        if cached.snapshot.degraded_reason_code
                        else "healthy"
                    ),
                    from_cache=True,
                    reason_code=cached.snapshot.degraded_reason_code,
                )
            attempted_monotonic = time.monotonic()
            attempted_at = self._now()
            try:
                snapshot = adapter.collect(force_refresh=force_refresh)
                if len(snapshot.models) > 100_000:
                    raise ValueError("model_inventory_source_limit_exceeded")
                successful = _CachedSource(
                    snapshot=snapshot,
                    last_attempt_monotonic=attempted_monotonic,
                    last_success_monotonic=time.monotonic(),
                    last_attempt_at=attempted_at,
                    last_success_at=self._now(),
                    last_refresh_was_forced=force_refresh,
                )
                self._cache[adapter.source_id] = successful
                return snapshot, self._status(
                    adapter,
                    successful,
                    status=(
                        "degraded" if snapshot.degraded_reason_code else "healthy"
                    ),
                    from_cache=False,
                    reason_code=snapshot.degraded_reason_code,
                )
            except Exception as exc:
                reason = self._reason_code(exc)
                if cached is None:
                    empty = ModelInventorySnapshot(models=())
                    return empty, ModelInventorySourceStatus(
                        source_id=adapter.source_id,
                        source_kind=adapter.source_kind,
                        status="unavailable",
                        last_attempt_at=attempted_at,
                        reason_code=reason,
                    )
                cached.last_attempt_monotonic = attempted_monotonic
                cached.last_attempt_at = attempted_at
                cached.last_refresh_was_forced = force_refresh
                stale = (
                    time.monotonic() - cached.last_success_monotonic
                    >= adapter.stale_after_seconds
                )
                return cached.snapshot, ModelInventorySourceStatus(
                    source_id=adapter.source_id,
                    source_kind=adapter.source_kind,
                    status="stale" if stale else "degraded",
                    stale=stale,
                    from_cache=True,
                    last_attempt_at=cached.last_attempt_at,
                    last_success_at=cached.last_success_at,
                    reason_code=reason,
                    model_count=len(cached.snapshot.models),
                )

    @staticmethod
    def _status(
        adapter: ModelSourceAdapterPort,
        cached: _CachedSource,
        *,
        status: str,
        from_cache: bool,
        reason_code: str | None = None,
    ) -> ModelInventorySourceStatus:
        stale = (
            time.monotonic() - cached.last_success_monotonic
            >= adapter.stale_after_seconds
        )
        return ModelInventorySourceStatus(
            source_id=adapter.source_id,
            source_kind=adapter.source_kind,
            status="stale" if stale else status,
            stale=stale,
            from_cache=from_cache,
            last_attempt_at=cached.last_attempt_at,
            last_success_at=cached.last_success_at,
            reason_code=reason_code,
            model_count=len(cached.snapshot.models),
        )

    @staticmethod
    def _merge(
        descriptors: list[ModelInventoryDescriptor],
    ) -> tuple[ModelInventoryDescriptor, ...]:
        grouped: dict[tuple[str, str, str], list[ModelInventoryDescriptor]] = {}
        for item in descriptors:
            grouped.setdefault(
                (item.provider_id, item.model_id, item.executor_id), []
            ).append(item)
        return tuple(
            ModelInventoryService._merge_group(group)
            for _identity, group in sorted(grouped.items())
        )

    @staticmethod
    def _merge_group(
        group: list[ModelInventoryDescriptor],
    ) -> ModelInventoryDescriptor:
        first = group[0]
        claims: dict[str, list[ModelCapabilityClaim]] = {}
        for item in group:
            for claim in item.capabilities:
                claims.setdefault(claim.capability_id, []).append(claim)
        merged_claims: list[ModelCapabilityClaim] = []
        conflicts = set(value for item in group for value in item.conflicts)
        for capability_id, values in sorted(claims.items()):
            known = {item.value for item in values if item.value != "unknown"}
            if len(known) > 1:
                conflicts.add(f"capability:{capability_id}")
                merged_claims.append(ModelCapabilityClaim(
                    capability_id=capability_id,
                    value="unknown",
                    evidence=ModelMetadataEvidence.UNKNOWN,
                ))
            else:
                merged_claims.append(next(
                    (item for item in values if item.value != "unknown"),
                    values[0],
                ))
        availabilities = {item.availability for item in group}
        availability = (
            ModelAvailability.AVAILABLE
            if ModelAvailability.AVAILABLE in availabilities
            else ModelAvailability.DEGRADED
            if ModelAvailability.DEGRADED in availabilities
            else ModelAvailability.UNAVAILABLE
            if availabilities == {ModelAvailability.UNAVAILABLE}
            else ModelAvailability.UNKNOWN
        )
        health_values = {item.health for item in group}
        health = (
            ModelHealth.HEALTHY
            if ModelHealth.HEALTHY in health_values
            else ModelHealth.DEGRADED
            if ModelHealth.DEGRADED in health_values
            else ModelHealth.UNAVAILABLE
            if health_values == {ModelHealth.UNAVAILABLE}
            else ModelHealth.UNKNOWN
        )
        return ModelInventoryDescriptor.model_validate({
            **first.model_dump(mode="python", by_alias=True),
            "source_ids": tuple(
                value for item in group for value in item.source_ids
            ),
            "source_kinds": tuple(sorted(
                {value for item in group for value in item.source_kinds},
                key=lambda value: value.value,
            )),
            "profile_ids": tuple(
                value for item in group for value in item.profile_ids
            ),
            "aliases": tuple(value for item in group for value in item.aliases),
            "availability": availability,
            "health": health,
            "configured": any(item.configured for item in group),
            "installed": (
                True if any(item.installed is True for item in group)
                else False if all(item.installed is False for item in group)
                else None
            ),
            "loaded": (
                True if any(item.loaded is True for item in group)
                else False if all(item.loaded is False for item in group)
                else None
            ),
            "listing_supported": any(item.listing_supported for item in group),
            "capabilities": tuple(merged_claims),
            "conflicts": tuple(sorted(conflicts)),
            "used_by_consumers": tuple(
                value for item in group for value in item.used_by_consumers
            ),
        })

    def _revision(self, models: tuple[ModelInventoryDescriptor, ...]) -> int:
        payload = "\n".join(
            item.model_dump_json(by_alias=True) for item in models
        )
        fingerprint = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        with self._revision_lock:
            if self._catalog_fingerprint and fingerprint != self._catalog_fingerprint:
                self._catalog_revision += 1
            self._catalog_fingerprint = fingerprint
            return self._catalog_revision

    @staticmethod
    def _reason_code(error: Exception) -> str:
        value = str(error or "").strip()
        if value.startswith(("model_", "provider_", "configured_", "cli_")) and all(
            character.isalnum() or character in "_.:-" for character in value
        ):
            return value[:160]
        return f"model_inventory_source_error:{type(error).__name__}"

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat().replace("+00:00", "Z")


__all__ = [
    "ModelInventoryService",
    "ModelInventorySnapshot",
    "ModelSourceAdapterPort",
]
