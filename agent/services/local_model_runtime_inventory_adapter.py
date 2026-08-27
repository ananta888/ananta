"""Observed local runtime source adapter for the canonical model inventory."""

from __future__ import annotations

from collections.abc import Callable

from agent.services.model_inventory_service import ModelInventorySnapshot
from ananta_contracts.local_model_runtime import LocalRuntimeSnapshot
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


class LocalRuntimeModelInventoryAdapter:
    source_id = "local.runtime"
    source_kind = ModelSourceKind.OBSERVED_RUNTIME
    cache_ttl_seconds = 5.0
    stale_after_seconds = 30.0

    def __init__(self, snapshot_loader: Callable[[], LocalRuntimeSnapshot]) -> None:
        self._snapshot_loader = snapshot_loader

    def collect(self, *, force_refresh: bool = False) -> ModelInventorySnapshot:
        snapshot = self._snapshot_loader()
        unavailable = [row for row in snapshot.runtimes if row.health.value == "unavailable"]
        return ModelInventorySnapshot(
            models=tuple(self._descriptor(row) for row in snapshot.runtimes),
            degraded_reason_code=("local_runtime_partial_unavailable" if unavailable else None),
        )

    def _descriptor(self, row) -> ModelInventoryDescriptor:
        availability = {
            "ready": ModelAvailability.AVAILABLE,
            "not_ready": ModelAvailability.UNAVAILABLE,
            "unknown": ModelAvailability.UNKNOWN,
        }[row.readiness.value]
        health = {
            "healthy": ModelHealth.HEALTHY,
            "degraded": ModelHealth.DEGRADED,
            "unavailable": ModelHealth.UNAVAILABLE,
            "unknown": ModelHealth.UNKNOWN,
        }[row.health.value]
        return ModelInventoryDescriptor(
            provider_id=row.provider_id,
            model_id=row.model_id,
            executor_id=f"candidate:{row.provider_id}" if row.candidate_only else f"api:{row.provider_id}",
            display_name=row.model_id,
            runtime=ModelRuntime.LOCAL,
            source_ids=(self.source_id,),
            source_kinds=(self.source_kind,),
            availability=availability,
            health=health,
            configured=True,
            installed=True,
            loaded=row.readiness.value == "ready",
            listing_supported=not row.candidate_only,
            context_window=row.effective_context,
            capabilities=tuple(
                ModelCapabilityClaim(
                    capability_id=capability,
                    value="supported",
                    evidence=ModelMetadataEvidence.DETECTED,
                    source_id=self.source_id,
                )
                for capability in row.capabilities
            ),
            metadata_facts=(
                ModelMetadataFact(
                    fact_id="readiness",
                    value=row.readiness.value,
                    evidence=ModelMetadataEvidence.DETECTED,
                    source_id=self.source_id,
                ),
                ModelMetadataFact(
                    fact_id="reason_code",
                    value=row.reason_code,
                    evidence=ModelMetadataEvidence.DETECTED,
                    source_id=self.source_id,
                ),
                ModelMetadataFact(
                    fact_id="vram_budget_bytes",
                    value=str(row.resources.vram_budget_bytes),
                    evidence=ModelMetadataEvidence.DECLARED,
                    source_id=self.source_id,
                ),
                ModelMetadataFact(
                    fact_id="ram_budget_bytes",
                    value=str(row.resources.ram_budget_bytes),
                    evidence=ModelMetadataEvidence.DECLARED,
                    source_id=self.source_id,
                ),
                ModelMetadataFact(
                    fact_id="candidate_only",
                    value=str(row.candidate_only).lower(),
                    evidence=ModelMetadataEvidence.DECLARED,
                    source_id=self.source_id,
                ),
                ModelMetadataFact(
                    fact_id="orchestration_authority",
                    value="false",
                    evidence=ModelMetadataEvidence.DECLARED,
                    source_id=self.source_id,
                ),
            ),
        )


__all__ = ["LocalRuntimeModelInventoryAdapter"]
