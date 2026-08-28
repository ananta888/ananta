"""Hub-owned immutable expert registry and atomic active-generation alias."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from ananta_contracts.parametric_knowledge import (
    KnowledgeExpertBank,
    KnowledgeExpertManifest,
    ParametricKnowledgeUnit,
    knowledge_unit_set_digest,
)


class KnowledgeExpertVerifierPort(Protocol):
    def verify_manifest(self, manifest: KnowledgeExpertManifest) -> None: ...

    def verify_bank(self, bank: KnowledgeExpertBank) -> None: ...


class KnowledgeExpertPromotionGatePort(Protocol):
    def evaluate(
        self,
        *,
        bank: KnowledgeExpertBank,
        manifests: Sequence[KnowledgeExpertManifest],
        evidence: Mapping[str, Any],
    ) -> None: ...


class KnowledgeExpertCacheInvalidationPort(Protocol):
    def invalidate_manifest(self, *, manifest_digest: str) -> None: ...


class ParametricKnowledgeUnitCatalogPort(Protocol):
    def get(self, *, unit_id: str) -> ParametricKnowledgeUnit | None: ...


class KnowledgeExpertRegistryStorePort(Protocol):
    def get_manifest(self, digest: str) -> KnowledgeExpertManifest | None: ...

    def put_manifest(self, manifest: KnowledgeExpertManifest) -> None: ...

    def get_bank(self, bank_id: str, generation_id: str) -> KnowledgeExpertBank | None: ...

    def put_bank(self, bank: KnowledgeExpertBank) -> None: ...

    def active_generation(self, bank_id: str) -> str: ...

    def compare_and_swap_active(self, bank_id: str, *, expected: str, replacement: str) -> bool: ...

    def revoke_manifest(self, digest: str) -> None: ...

    def is_manifest_revoked(self, digest: str) -> bool: ...


@dataclass(slots=True)
class InMemoryKnowledgeExpertRegistryStore:
    manifests: dict[str, KnowledgeExpertManifest] = field(default_factory=dict)
    banks: dict[tuple[str, str], KnowledgeExpertBank] = field(default_factory=dict)
    aliases: dict[str, str] = field(default_factory=dict)
    revoked_manifests: set[str] = field(default_factory=set)

    def get_manifest(self, digest: str) -> KnowledgeExpertManifest | None:
        return self.manifests.get(digest)

    def put_manifest(self, manifest: KnowledgeExpertManifest) -> None:
        current = self.manifests.get(manifest.manifest_digest)
        if current is not None and current != manifest:
            raise ValueError("knowledge_expert_manifest_digest_collision")
        self.manifests[manifest.manifest_digest] = manifest

    def get_bank(self, bank_id: str, generation_id: str) -> KnowledgeExpertBank | None:
        return self.banks.get((bank_id, generation_id))

    def put_bank(self, bank: KnowledgeExpertBank) -> None:
        key = (bank.bank_id, bank.generation_id)
        current = self.banks.get(key)
        if current is not None and current != bank:
            raise ValueError("knowledge_expert_bank_generation_immutable")
        self.banks[key] = bank

    def active_generation(self, bank_id: str) -> str:
        return self.aliases.get(bank_id, "")

    def compare_and_swap_active(self, bank_id: str, *, expected: str, replacement: str) -> bool:
        if self.active_generation(bank_id) != expected:
            return False
        self.aliases[bank_id] = replacement
        return True

    def revoke_manifest(self, digest: str) -> None:
        self.revoked_manifests.add(digest)

    def is_manifest_revoked(self, digest: str) -> bool:
        return digest in self.revoked_manifests


class KnowledgeExpertRegistryService:
    def __init__(
        self,
        *,
        store: KnowledgeExpertRegistryStorePort,
        verifier: KnowledgeExpertVerifierPort,
        promotion_gate: KnowledgeExpertPromotionGatePort,
        cache_invalidator: KnowledgeExpertCacheInvalidationPort,
        unit_catalog: ParametricKnowledgeUnitCatalogPort,
    ) -> None:
        self._store = store
        self._verifier = verifier
        self._promotion_gate = promotion_gate
        self._cache_invalidator = cache_invalidator
        self._unit_catalog = unit_catalog

    def register_manifest(self, manifest: KnowledgeExpertManifest) -> str:
        self._verifier.verify_manifest(manifest)
        if manifest.evaluation_status != "passed":
            raise ValueError("knowledge_expert_evaluation_not_passed")
        if not manifest.compatibility.kv_cache_safe or manifest.compatibility.target_layer != "final_ffn":
            raise ValueError("knowledge_expert_runtime_compatibility_denied")
        units: list[ParametricKnowledgeUnit] = []
        for unit_id in manifest.knowledge_unit_ids:
            unit = self._unit_catalog.get(unit_id=unit_id)
            if unit is None or unit.revoked:
                raise ValueError("knowledge_expert_unit_unavailable")
            if (unit.tenant_id, unit.workspace_id, unit.repository_id) != (
                manifest.tenant_id,
                manifest.workspace_id,
                manifest.repository_id,
            ):
                raise ValueError("knowledge_expert_unit_scope_mismatch")
            units.append(unit)
        if knowledge_unit_set_digest(units) != manifest.knowledge_unit_digest:
            raise ValueError("knowledge_expert_unit_digest_mismatch")
        self._store.put_manifest(manifest)
        return manifest.manifest_digest

    def register_bank(self, bank: KnowledgeExpertBank, *, promotion_evidence: Mapping[str, Any]) -> str:
        self._verifier.verify_bank(bank)
        if bank.status not in {"candidate", "admitted"}:
            raise ValueError("knowledge_expert_bank_registration_status_invalid")
        manifests: list[KnowledgeExpertManifest] = []
        for digest in bank.expert_manifest_digests:
            manifest = self._store.get_manifest(digest)
            if manifest is None or self._store.is_manifest_revoked(digest):
                raise ValueError("knowledge_expert_bank_manifest_unavailable")
            if (manifest.tenant_id, manifest.workspace_id, manifest.repository_id) != (
                bank.tenant_id,
                bank.workspace_id,
                bank.repository_id,
            ):
                raise ValueError("knowledge_expert_bank_scope_mismatch")
            manifests.append(manifest)
        self._promotion_gate.evaluate(bank=bank, manifests=manifests, evidence=promotion_evidence)
        self._store.put_bank(bank)
        return bank.bank_digest

    def activate(
        self,
        *,
        bank_id: str,
        generation_id: str,
        expected_active_generation: str,
    ) -> Mapping[str, str]:
        bank = self._store.get_bank(bank_id, generation_id)
        if bank is None or bank.status != "admitted":
            raise ValueError("knowledge_expert_bank_not_admitted")
        if any(self._store.is_manifest_revoked(digest) for digest in bank.expert_manifest_digests):
            raise ValueError("knowledge_expert_bank_contains_revoked_manifest")
        if not self._store.compare_and_swap_active(
            bank_id,
            expected=expected_active_generation,
            replacement=generation_id,
        ):
            raise ValueError("knowledge_expert_bank_activation_conflict")
        return {
            "bank_id": bank_id,
            "active_generation_id": generation_id,
            "previous_generation_id": expected_active_generation,
        }

    def switch(
        self,
        *,
        bank_id: str,
        expected_generation_id: str,
        target_generation_id: str,
    ) -> bool:
        """Idempotently switch one admitted generation for automatic rollout."""

        current = self._store.active_generation(bank_id)
        if current == target_generation_id:
            return True
        if current != expected_generation_id:
            return False
        try:
            self.activate(
                bank_id=bank_id,
                generation_id=target_generation_id,
                expected_active_generation=expected_generation_id,
            )
        except ValueError:
            return False
        return True

    def revoke_manifest(self, digest: str) -> None:
        if self._store.get_manifest(digest) is None:
            raise ValueError("knowledge_expert_manifest_unknown")
        self._store.revoke_manifest(digest)
        self._cache_invalidator.invalidate_manifest(manifest_digest=digest)


__all__ = [
    "InMemoryKnowledgeExpertRegistryStore",
    "KnowledgeExpertRegistryService",
    "KnowledgeExpertRegistryStorePort",
    "KnowledgeExpertPromotionGatePort",
    "KnowledgeExpertCacheInvalidationPort",
    "KnowledgeExpertVerifierPort",
    "ParametricKnowledgeUnitCatalogPort",
]
