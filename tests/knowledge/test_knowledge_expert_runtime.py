from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path

import pytest

from ananta_contracts.knowledge_expert_runtime import KnowledgeExpertRuntimeCapability
from ananta_contracts.parametric_knowledge import KnowledgeExpertManifest
from worker.inference.knowledge_expert_capability_probe import KnowledgeExpertCapabilityProbe
from worker.inference.knowledge_expert_resource_planner import KnowledgeExpertResourcePlanner
from worker.inference.knowledge_expert_runtime import KnowledgeExpertResidencyManager
from worker.retrieval.knowledge_expert_router import (
    KnowledgeExpertCandidate,
    UncertaintyAwareKnowledgeExpertRouter,
)


def _capability(**overrides) -> KnowledgeExpertRuntimeCapability:
    payload = {
        "schema": "ananta.knowledge-expert-runtime-capability.v1",
        "provider_id": "runtime-test",
        "provider_version": "1",
        "base_model_digest": "a" * 64,
        "tokenizer_digest": "b" * 64,
        "architecture": "llama",
        "final_layer_name": "model.layers.15.mlp",
        "supported_target_modules": ["gate_proj", "up_proj", "down_proj"],
        "dynamic_adapter_composition": True,
        "token_entropy": True,
        "kv_cache_safe_final_ffn": True,
        "atomic_expert_switch": True,
        "max_active_experts": 4,
    }
    payload.update(overrides)
    return KnowledgeExpertRuntimeCapability.from_mapping(payload)


def _manifest(data: bytes, *, expert_id: str = "expert-1", unit_id: str = "unit-1") -> KnowledgeExpertManifest:
    return KnowledgeExpertManifest.from_mapping(
        {
            "schema": "ananta.knowledge-expert-manifest.v1",
            "expert_id": expert_id,
            "generation_id": "generation-1",
            "tenant_id": "tenant-1",
            "workspace_id": "workspace-1",
            "repository_id": "repo-1",
            "knowledge_unit_ids": [unit_id],
            "knowledge_unit_digest": "c" * 64,
            "adapter_format": "safetensors",
            "adapter_digest": hashlib.sha256(data).hexdigest(),
            "adapter_size_bytes": len(data),
            "compatibility": {
                "base_model_digest": "a" * 64,
                "tokenizer_digest": "b" * 64,
                "architecture": "llama",
                "target_layer": "final_ffn",
                "target_modules": ["gate_proj", "up_proj", "down_proj"],
                "runtime_provider": "runtime-test",
                "runtime_version": "1",
                "kv_cache_safe": True,
            },
            "peft_configuration_digest": "d" * 64,
            "training_dataset_digest": "e" * 64,
            "evaluation_status": "passed",
            "evaluation_digest": "f" * 64,
            "policy_decision_digest": "1" * 64,
            "signing_key_id": "key-1",
            "signature": "test-signature",
        }
    )


class _Resolver:
    def __init__(self, paths: dict[str, Path]) -> None:
        self.paths = paths

    def resolve(self, *, adapter_digest: str) -> Path:
        return self.paths[adapter_digest]


class _Runtime:
    def __init__(self, *, fail_activate: bool = False) -> None:
        self.loaded: list[str] = []
        self.unloaded: list[str] = []
        self.activations: list[tuple[tuple[str, ...], str]] = []
        self.fail_activate = fail_activate

    def load(self, *, expert_id: str, adapter_path: Path, manifest: KnowledgeExpertManifest) -> None:
        self.loaded.append(expert_id)

    def activate_atomic(self, *, expert_ids, generation_id: str) -> None:
        if self.fail_activate:
            raise RuntimeError("atomic switch failed")
        self.activations.append((tuple(expert_ids), generation_id))

    def unload(self, *, expert_id: str) -> None:
        self.unloaded.append(expert_id)


def test_residency_verifies_artifacts_and_activates_atomically(tmp_path: Path):
    data = b"safe tensor fixture"
    path = tmp_path / "expert.safetensors"
    path.write_bytes(data)
    manifest = _manifest(data)
    runtime = _Runtime()
    manager = KnowledgeExpertResidencyManager(
        resolver=_Resolver({manifest.adapter_digest: path}),
        runtime=runtime,
        maximum_adapter_bytes=1024,
    )

    plan = manager.activate(
        [manifest],
        generation_id="generation-1",
        expected_generation_id="",
        capability=_capability(),
    )

    assert plan.expert_ids == ("expert-1",)
    assert runtime.activations == [(("expert-1",), "generation-1")]
    assert manager.active_generation == "generation-1"


def test_failed_atomic_activation_cleans_new_residency(tmp_path: Path):
    data = b"safe tensor fixture"
    path = tmp_path / "expert.safetensors"
    path.write_bytes(data)
    manifest = _manifest(data)
    runtime = _Runtime(fail_activate=True)
    manager = KnowledgeExpertResidencyManager(
        resolver=_Resolver({manifest.adapter_digest: path}),
        runtime=runtime,
        maximum_adapter_bytes=1024,
    )

    with pytest.raises(RuntimeError, match="atomic switch failed"):
        manager.activate([manifest], generation_id="generation-1", expected_generation_id="", capability=_capability())

    assert runtime.unloaded == ["expert-1"]
    assert manager.active_generation == ""


@pytest.mark.parametrize(
    "capability",
    [
        _capability(kv_cache_safe_final_ffn=False),
        _capability(atomic_expert_switch=False),
        _capability(dynamic_adapter_composition=False),
        _capability(base_model_digest="9" * 64),
    ],
)
def test_runtime_capability_fails_closed(tmp_path: Path, capability):
    data = b"safe tensor fixture"
    path = tmp_path / "expert.safetensors"
    path.write_bytes(data)
    manifest = _manifest(data)
    manager = KnowledgeExpertResidencyManager(
        resolver=_Resolver({manifest.adapter_digest: path}),
        runtime=_Runtime(),
        maximum_adapter_bytes=1024,
    )
    with pytest.raises(ValueError):
        manager.activate([manifest], generation_id="generation-1", expected_generation_id="", capability=capability)


def test_composition_rejects_overlapping_units(tmp_path: Path):
    data = b"safe tensor fixture"
    path = tmp_path / "expert.safetensors"
    path.write_bytes(data)
    first = _manifest(data)
    second = replace(first, expert_id="expert-2", adapter_digest=first.adapter_digest)
    manager = KnowledgeExpertResidencyManager(
        resolver=_Resolver({first.adapter_digest: path}), runtime=_Runtime(), maximum_adapter_bytes=1024
    )
    with pytest.raises(ValueError, match="knowledge_expert_composition_conflict"):
        manager.activate(
            [first, second],
            generation_id="generation-1",
            expected_generation_id="",
            capability=_capability(),
        )


class _Candidates:
    def search(self, *, query: str, top_k: int):
        return [
            KnowledgeExpertCandidate("a" * 64, "tenant-2", "workspace-1", "repo-1", 1.0),
            KnowledgeExpertCandidate("b" * 64, "tenant-1", "workspace-1", "repo-1", 0.9),
        ]


def test_router_scope_and_hysteresis_are_fail_closed():
    router = UncertaintyAwareKnowledgeExpertRouter(candidates=_Candidates(), threshold=2.0, hysteresis_tokens=8)
    scope = {"tenant_id": "tenant-1", "workspace_id": "workspace-1", "repository_id": "repo-1"}
    selected = router.decide(
        query="retry policy",
        entropy=2.1,
        token_index=10,
        generation_id="g1",
        scope=scope,
        mode="auto",
        citation_required=False,
        capability_ready=True,
    )
    held = router.decide(
        query="retry policy",
        entropy=2.2,
        token_index=12,
        generation_id="g1",
        scope=scope,
        mode="auto",
        citation_required=False,
        capability_ready=True,
    )
    assert selected.candidate_manifest_digests == ("b" * 64,)
    assert held.reason_code == "switch_hysteresis"

    unbound = router.decide(
        query="retry policy",
        entropy=2.2,
        token_index=30,
        generation_id="",
        scope=scope,
        mode="auto",
        citation_required=False,
        capability_ready=True,
    )
    assert unbound.execute is False
    assert unbound.reason_code == "router_input_invalid"


def test_resource_planner_falls_back_before_expected_oom():
    plan = KnowledgeExpertResourcePlanner().plan(
        base_model_vram_bytes=9_000,
        kv_cache_vram_bytes=2_000,
        adapter_sizes=(500,),
        vram_capacity_bytes=10_000,
        vram_reserve_bytes=500,
        ram_capacity_bytes=10_000,
        disk_capacity_bytes=10_000,
        warm_cache_experts=1,
    )
    assert plan.admitted is False
    assert plan.fallback_mode == "rag_only"
    assert plan.reason_code == "base_and_kv_vram_exceeded"

    with pytest.raises(ValueError, match="resource_input_invalid"):
        KnowledgeExpertResourcePlanner().plan(
            base_model_vram_bytes=9_000.5,  # type: ignore[arg-type]
            kv_cache_vram_bytes=2_000,
            adapter_sizes=(500,),
            vram_capacity_bytes=10_000,
            vram_reserve_bytes=500,
            ram_capacity_bytes=10_000,
            disk_capacity_bytes=10_000,
            warm_cache_experts=1,
        )


class _Introspection:
    def __init__(self, capability, *, passed=True):
        self.capability = capability
        self.passed = passed

    def inspect(self):
        names = {
            "final_ffn_probe_passed",
            "entropy_probe_passed",
            "composition_probe_passed",
            "kv_cache_probe_passed",
            "atomic_switch_probe_passed",
        }
        return {
            "proofs": sorted(names),
            "proof_results": {name: self.passed for name in names},
            "capability": self.capability.to_dict(),
        }


def test_capability_probe_requires_explicit_successful_proofs():
    expected = _capability()
    assert KnowledgeExpertCapabilityProbe(_Introspection(expected)).probe() == expected
    with pytest.raises(ValueError, match="runtime_probe_failed"):
        KnowledgeExpertCapabilityProbe(_Introspection(expected, passed=False)).probe()
