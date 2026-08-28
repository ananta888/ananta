"""Cache-safe Worker composition and residency for Hub-admitted experts."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ananta_contracts.knowledge_expert_runtime import KnowledgeExpertRuntimeCapability
from ananta_contracts.parametric_knowledge import KnowledgeExpertManifest


class ExpertArtifactResolverPort(Protocol):
    def resolve(self, *, adapter_digest: str) -> Path: ...


class DynamicExpertRuntimePort(Protocol):
    def load(self, *, expert_id: str, adapter_path: Path, manifest: KnowledgeExpertManifest) -> None: ...

    def activate_atomic(self, *, expert_ids: Sequence[str], generation_id: str) -> None: ...

    def unload(self, *, expert_id: str) -> None: ...


@dataclass(frozen=True, slots=True)
class ExpertCompositionPlan:
    generation_id: str
    expert_ids: tuple[str, ...]
    manifest_digests: tuple[str, ...]
    total_adapter_bytes: int


class KnowledgeExpertCompositionPolicy:
    def plan(
        self,
        manifests: Sequence[KnowledgeExpertManifest],
        *,
        generation_id: str,
        capability: KnowledgeExpertRuntimeCapability,
    ) -> ExpertCompositionPlan:
        items = tuple(manifests)
        if not str(generation_id).strip() or not items or len(items) > capability.max_active_experts:
            raise ValueError("knowledge_expert_composition_count_denied")
        if not (
            capability.dynamic_adapter_composition
            and capability.kv_cache_safe_final_ffn
            and capability.atomic_expert_switch
        ):
            raise ValueError("knowledge_expert_runtime_capability_missing")
        unit_ids: set[str] = set()
        expert_ids: set[str] = set()
        for manifest in items:
            compatibility = manifest.compatibility
            if (
                compatibility.base_model_digest != capability.base_model_digest
                or compatibility.tokenizer_digest != capability.tokenizer_digest
                or compatibility.architecture != capability.architecture
                or compatibility.runtime_provider != capability.provider_id
                or compatibility.runtime_version != capability.provider_version
                or compatibility.target_layer != "final_ffn"
                or not compatibility.kv_cache_safe
                or not set(compatibility.target_modules).issubset(capability.supported_target_modules)
            ):
                raise ValueError("knowledge_expert_runtime_binding_mismatch")
            if manifest.expert_id in expert_ids:
                raise ValueError("knowledge_expert_composition_duplicate")
            if unit_ids.intersection(manifest.knowledge_unit_ids):
                raise ValueError("knowledge_expert_composition_conflict")
            expert_ids.add(manifest.expert_id)
            unit_ids.update(manifest.knowledge_unit_ids)
        ordered = tuple(sorted(items, key=lambda item: item.expert_id))
        return ExpertCompositionPlan(
            generation_id=str(generation_id),
            expert_ids=tuple(item.expert_id for item in ordered),
            manifest_digests=tuple(item.manifest_digest for item in ordered),
            total_adapter_bytes=sum(item.adapter_size_bytes for item in ordered),
        )


class KnowledgeExpertResidencyManager:
    def __init__(
        self,
        *,
        resolver: ExpertArtifactResolverPort,
        runtime: DynamicExpertRuntimePort,
        composition: KnowledgeExpertCompositionPolicy | None = None,
        maximum_adapter_bytes: int,
    ) -> None:
        self._resolver = resolver
        self._runtime = runtime
        self._composition = composition or KnowledgeExpertCompositionPolicy()
        self._maximum_adapter_bytes = max(1, int(maximum_adapter_bytes))
        self._active_generation = ""
        self._active_expert_ids: tuple[str, ...] = ()
        self._loaded: set[str] = set()

    @property
    def active_generation(self) -> str:
        return self._active_generation

    def activate(
        self,
        manifests: Sequence[KnowledgeExpertManifest],
        *,
        generation_id: str,
        expected_generation_id: str,
        capability: KnowledgeExpertRuntimeCapability,
    ) -> ExpertCompositionPlan:
        if expected_generation_id != self._active_generation:
            raise ValueError("knowledge_expert_runtime_generation_conflict")
        plan = self._composition.plan(manifests, generation_id=generation_id, capability=capability)
        if plan.total_adapter_bytes > self._maximum_adapter_bytes:
            raise ValueError("knowledge_expert_runtime_residency_budget_exceeded")
        if generation_id == self._active_generation and plan.expert_ids == self._active_expert_ids:
            return plan
        manifest_by_id = {manifest.expert_id: manifest for manifest in manifests}
        newly_loaded: list[str] = []
        try:
            for expert_id in plan.expert_ids:
                if expert_id in self._loaded:
                    continue
                manifest = manifest_by_id[expert_id]
                path = self._verified_path(manifest)
                self._runtime.load(expert_id=expert_id, adapter_path=path, manifest=manifest)
                self._loaded.add(expert_id)
                newly_loaded.append(expert_id)
            self._runtime.activate_atomic(expert_ids=plan.expert_ids, generation_id=generation_id)
        except Exception:
            for expert_id in reversed(newly_loaded):
                self._runtime.unload(expert_id=expert_id)
                self._loaded.discard(expert_id)
            raise
        evicted = tuple(expert_id for expert_id in self._active_expert_ids if expert_id not in plan.expert_ids)
        self._active_generation = generation_id
        self._active_expert_ids = plan.expert_ids
        for expert_id in evicted:
            self._runtime.unload(expert_id=expert_id)
            self._loaded.discard(expert_id)
        return plan

    def _verified_path(self, manifest: KnowledgeExpertManifest) -> Path:
        path = self._resolver.resolve(adapter_digest=manifest.adapter_digest)
        if path.suffix != ".safetensors" or path.is_symlink() or not path.is_file():
            raise ValueError("knowledge_expert_artifact_path_denied")
        if path.stat().st_size != manifest.adapter_size_bytes:
            raise ValueError("knowledge_expert_artifact_size_mismatch")
        hasher = hashlib.sha256()
        with path.open("rb") as artifact:
            while block := artifact.read(1024 * 1024):
                hasher.update(block)
        digest = hasher.hexdigest()
        if digest != manifest.adapter_digest:
            raise ValueError("knowledge_expert_artifact_digest_mismatch")
        return path


__all__ = [
    "DynamicExpertRuntimePort",
    "ExpertArtifactResolverPort",
    "ExpertCompositionPlan",
    "KnowledgeExpertCompositionPolicy",
    "KnowledgeExpertResidencyManager",
]
