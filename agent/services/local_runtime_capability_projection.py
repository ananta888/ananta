"""Content-free API projection over the persisted runtime snapshot cache."""

from __future__ import annotations

from agent.services.local_runtime_capability_cache import LocalRuntimeCapabilityCache


class LocalRuntimeCapabilityProjection:
    def __init__(self, cache: LocalRuntimeCapabilityCache) -> None:
        self._cache = cache

    def snapshot(self) -> dict:
        snapshots = self._cache.load()
        providers = sorted({item.provider_id for item in snapshots})
        stale = any(item.stale for item in snapshots)
        routable = any(
            item.routable(capability)
            for item in snapshots
            for capability in ("chat", "embedding")
        )
        return {
            "schema": "ananta.local-runtime-capability-catalog.v1",
            "partial": stale,
            "providers": providers,
            "snapshots": [item.to_dict() for item in snapshots],
            "health": {
                "runtime": {
                    "status": "available" if snapshots else "unknown",
                    "reason_code": None if snapshots else "local_runtime_snapshot_empty",
                },
                "discovery": {
                    "status": "stale" if stale else "healthy" if snapshots else "unknown",
                    "reason_code": "local_runtime_capability_snapshot_stale" if stale else None,
                },
                "detail": {
                    "status": "degraded" if any(not item.capabilities for item in snapshots) else "healthy",
                    "reason_code": (
                        "local_runtime_detail_metadata_missing"
                        if any(not item.capabilities for item in snapshots)
                        else None
                    ),
                },
                "cache": {
                    "status": "degraded" if stale else "healthy" if snapshots else "empty",
                    "reason_code": "local_runtime_capability_cache_empty" if not snapshots else None,
                },
                "routing": {
                    "status": "healthy" if routable else "unavailable",
                    "reason_code": None if routable else "local_runtime_no_routable_model",
                },
            },
        }


__all__ = ["LocalRuntimeCapabilityProjection"]
