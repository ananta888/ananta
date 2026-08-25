"""Adapter from the compatible catalog v1 read model into inventory v2."""

from __future__ import annotations

from collections.abc import Callable

from agent.services.model_inventory_service import ModelInventorySnapshot
from ananta_contracts.model_catalog import (
    ModelCapabilityClaim,
    ModelCatalog,
    ModelInventoryDescriptor,
    ModelMetadataEvidence,
    ModelSourceKind,
)


class ProviderCatalogModelInventoryAdapter:
    source_id = "providers.catalog"
    source_kind = ModelSourceKind.DISCOVERED
    cache_ttl_seconds = 30.0
    stale_after_seconds = 180.0

    def __init__(self, loader: Callable[[bool], ModelCatalog]) -> None:
        self._loader = loader

    def collect(self, *, force_refresh: bool = False) -> ModelInventorySnapshot:
        catalog = self._loader(force_refresh)
        return ModelInventorySnapshot(
            models=tuple(
                ModelInventoryDescriptor(
                    provider_id=item.provider_id,
                    model_id=item.model_id,
                    executor_id=f"api:{item.provider_id}",
                    display_name=item.display_name,
                    runtime=item.runtime,
                    source_ids=(self.source_id,),
                    source_kinds=(self.source_kind,),
                    availability=item.availability,
                    health=item.health,
                    configured=item.is_default,
                    loaded=item.loaded,
                    listing_supported=True,
                    context_window=item.context_window,
                    quantization=item.quantization,
                    capabilities=tuple(
                        ModelCapabilityClaim(
                            capability_id=value,
                            value="supported",
                            evidence=ModelMetadataEvidence.DETECTED,
                            source_id=self.source_id,
                        )
                        for value in item.capabilities
                    ),
                )
                for item in catalog.models
            ),
            degraded_reason_code=(
                "provider_catalog_partial"
                if catalog.provider_failures
                else None
            ),
        )


__all__ = ["ProviderCatalogModelInventoryAdapter"]
