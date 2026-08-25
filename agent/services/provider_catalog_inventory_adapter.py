"""Adapter from the compatible catalog v1 read model into inventory v2."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from agent.services.model_inventory_service import ModelInventorySnapshot
from ananta_contracts.model_catalog import (
    ModelCapabilityClaim,
    ModelCatalog,
    ModelInventoryDescriptor,
    ModelMetadataEvidence,
    ModelMetadataFact,
    ModelRuntime,
    ModelSourceKind,
)


class ProviderCatalogModelInventoryAdapter:
    source_id = "providers.catalog"
    source_kind = ModelSourceKind.DISCOVERED
    cache_ttl_seconds = 30.0
    stale_after_seconds = 180.0

    def __init__(
        self,
        loader: Callable[[bool], ModelCatalog],
        remote_metadata: Callable[[], Mapping[str, Mapping[str, object]]] | None = None,
    ) -> None:
        self._loader = loader
        self._remote_metadata = remote_metadata or (lambda: {})

    def collect(self, *, force_refresh: bool = False) -> ModelInventorySnapshot:
        catalog = self._loader(force_refresh)
        remote = self._remote_metadata()
        return ModelInventorySnapshot(
            models=tuple(
                ModelInventoryDescriptor(
                    provider_id=item.provider_id,
                    model_id=item.model_id,
                    executor_id=f"api:{item.provider_id}",
                    display_name=item.display_name,
                    runtime=item.runtime,
                    source_ids=(self.source_id,),
                    source_kinds=(
                        (ModelSourceKind.REMOTE,)
                        if item.runtime is ModelRuntime.REMOTE
                        else (self.source_kind,)
                    ),
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
                    metadata_facts=self._remote_facts(
                        item.provider_id, remote.get(item.provider_id)
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

    def _remote_facts(
        self,
        provider_id: str,
        metadata: Mapping[str, object] | None,
    ) -> tuple[ModelMetadataFact, ...]:
        if not metadata:
            return ()
        facts: list[ModelMetadataFact] = []
        trust_level = str(metadata.get("trust_level") or "").strip()
        if trust_level:
            facts.append(ModelMetadataFact(
                fact_id="remote_trust_level",
                value=trust_level,
                evidence=ModelMetadataEvidence.DECLARED,
                source_id=self.source_id,
            ))
        max_hops = metadata.get("max_hops")
        if isinstance(max_hops, int) and not isinstance(max_hops, bool) and max_hops > 0:
            facts.append(ModelMetadataFact(
                fact_id="remote_hop_limit",
                value=str(max_hops),
                evidence=ModelMetadataEvidence.DECLARED,
                source_id=self.source_id,
            ))
        return tuple(facts)


__all__ = ["ProviderCatalogModelInventoryAdapter"]
