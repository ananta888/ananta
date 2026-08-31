"""Hub coordinator for registry tombstones and bounded artifact cleanup."""

from __future__ import annotations

from typing import Any

from agent.services.dendritic_memory_artifact_service import DendriticMemoryArtifactService
from agent.services.dendritic_memory_registry_service import DendriticMemoryRegistryService
from ananta_contracts.dendritic_memory import DendriticMemoryPackManifestV1


class DendriticMemoryLifecycleService:
    def __init__(
        self,
        *,
        registry: DendriticMemoryRegistryService,
        artifacts: DendriticMemoryArtifactService,
    ) -> None:
        self._registry = registry
        self._artifacts = artifacts

    def delete(
        self,
        *,
        tenant_id: str,
        pack_digest: str,
        expected_revision: int,
        idempotency_key: str,
    ) -> dict[str, Any]:
        current = self._registry.get(tenant_id=tenant_id, pack_digest=pack_digest)
        manifest = DendriticMemoryPackManifestV1.from_mapping(current["manifest"])
        tombstone = self._registry.delete(
            tenant_id=tenant_id,
            pack_digest=pack_digest,
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
        )
        cleanup = self._artifacts.delete(manifest=manifest)
        return {**tombstone, "artifact_cleanup": cleanup}


__all__ = ["DendriticMemoryLifecycleService"]
