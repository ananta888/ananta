"""Canonical ModelCatalog-v2 adapter for persisted runtime capabilities."""

from __future__ import annotations

from agent.services.local_runtime_capability_cache import LocalRuntimeCapabilityCache
from agent.services.model_inventory_service import ModelInventorySnapshot
from ananta_contracts.model_catalog import (
    ModelAvailability,
    ModelCapabilityClaim,
    ModelHealth,
    ModelInventoryDescriptor,
    ModelMetadataEvidence,
    ModelMetadataFact,
    ModelRuntime,
    ModelSourceKind,
)


class LocalRuntimeCapabilityInventoryAdapter:
    source_id = "local.runtime.capabilities"
    source_kind = ModelSourceKind.OBSERVED_RUNTIME
    cache_ttl_seconds = 5.0
    stale_after_seconds = 60.0

    def __init__(self, cache: LocalRuntimeCapabilityCache) -> None:
        self._cache = cache

    def collect(self, *, force_refresh: bool = False) -> ModelInventorySnapshot:
        snapshots = self._cache.load()
        return ModelInventorySnapshot(
            models=tuple(self._descriptor(item) for item in snapshots),
            degraded_reason_code=(
                "local_runtime_capability_snapshot_stale"
                if any(item.stale for item in snapshots)
                else None
            ),
        )

    def _descriptor(self, snapshot) -> ModelInventoryDescriptor:
        evidence = {
            "runtime_reported": ModelMetadataEvidence.DETECTED,
            "profile_declared": ModelMetadataEvidence.DECLARED,
            "observed_success": ModelMetadataEvidence.DETECTED,
            "observed_failure": ModelMetadataEvidence.DETECTED,
            "heuristic": ModelMetadataEvidence.UNKNOWN,
        }
        positive = tuple(item for item in snapshot.capabilities if item.supported)
        input_modalities = ["text"]
        if any(item.name == "vision" for item in positive):
            input_modalities.append("image")
        return ModelInventoryDescriptor(
            provider_id=snapshot.provider_id,
            model_id=snapshot.model_id,
            executor_id=f"api:{snapshot.provider_id}",
            display_name=snapshot.model_id,
            runtime=ModelRuntime.LOCAL,
            source_ids=(self.source_id,),
            source_kinds=(self.source_kind,),
            availability=ModelAvailability.DEGRADED if snapshot.stale else ModelAvailability.AVAILABLE,
            health=ModelHealth.DEGRADED if snapshot.stale else ModelHealth.HEALTHY,
            configured=True,
            installed=True,
            loaded=None,
            listing_supported=True,
            context_window=snapshot.context_window,
            input_modalities=tuple(input_modalities),
            output_modalities=("embedding",) if snapshot.model_kind == "embedding" else ("text",),
            capabilities=tuple(
                ModelCapabilityClaim(
                    capability_id=item.name,
                    value="supported" if item.supported else "unsupported",
                    evidence=evidence[item.source],
                    source_id=self.source_id,
                )
                for item in snapshot.capabilities
            ),
            metadata_facts=tuple(
                ModelMetadataFact(
                    fact_id=f"capability.{item.name}.source",
                    value=item.source,
                    evidence=evidence[item.source],
                    source_id=self.source_id,
                    confidence=item.confidence,
                )
                for item in snapshot.capabilities
            ) + (
                ModelMetadataFact(
                    fact_id="template.family",
                    value=snapshot.template_family,
                    evidence=ModelMetadataEvidence.DETECTED,
                    source_id=self.source_id,
                ),
                ModelMetadataFact(
                    fact_id="snapshot.sha256",
                    value=snapshot.snapshot_sha256,
                    evidence=ModelMetadataEvidence.DETECTED,
                    source_id=self.source_id,
                ),
            ),
            conflicts=snapshot.conflicts,
        )


__all__ = ["LocalRuntimeCapabilityInventoryAdapter"]
