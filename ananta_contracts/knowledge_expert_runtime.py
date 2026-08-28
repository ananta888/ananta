"""Runtime-neutral capability and activation contracts for knowledge experts."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any, Mapping

_DIGEST = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class KnowledgeExpertRuntimeCapability:
    schema: str
    provider_id: str
    provider_version: str
    base_model_digest: str
    tokenizer_digest: str
    architecture: str
    final_layer_name: str
    supported_target_modules: tuple[str, ...]
    dynamic_adapter_composition: bool
    token_entropy: bool
    kv_cache_safe_final_ffn: bool
    atomic_expert_switch: bool
    max_active_experts: int

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "KnowledgeExpertRuntimeCapability":
        allowed = {
            "schema",
            "provider_id",
            "provider_version",
            "base_model_digest",
            "tokenizer_digest",
            "architecture",
            "final_layer_name",
            "supported_target_modules",
            "dynamic_adapter_composition",
            "token_entropy",
            "kv_cache_safe_final_ffn",
            "atomic_expert_switch",
            "max_active_experts",
        }
        if not isinstance(raw, Mapping) or set(raw).difference(allowed):
            raise ValueError("knowledge_expert_runtime_capability_shape_invalid")
        if raw.get("schema") != "ananta.knowledge-expert-runtime-capability.v1":
            raise ValueError("knowledge_expert_runtime_capability_schema_invalid")
        for field in ("base_model_digest", "tokenizer_digest"):
            if not _DIGEST.fullmatch(str(raw.get(field) or "")):
                raise ValueError(f"knowledge_expert_runtime_{field}_invalid")
        modules = raw.get("supported_target_modules")
        if (
            not isinstance(modules, list)
            or not modules
            or len(modules) > 32
            or any(not isinstance(item, str) or not item.strip() or len(item) > 192 for item in modules)
            or len(set(modules)) != len(modules)
        ):
            raise ValueError("knowledge_expert_runtime_target_modules_invalid")
        maximum = raw.get("max_active_experts")
        if isinstance(maximum, bool) or not isinstance(maximum, int) or not 1 <= maximum <= 64:
            raise ValueError("knowledge_expert_runtime_active_experts_invalid")
        boolean_fields = (
            "dynamic_adapter_composition",
            "token_entropy",
            "kv_cache_safe_final_ffn",
            "atomic_expert_switch",
        )
        if any(not isinstance(raw.get(field), bool) for field in boolean_fields):
            raise ValueError("knowledge_expert_runtime_capability_boolean_invalid")
        text_fields = ("provider_id", "provider_version", "architecture", "final_layer_name")
        if any(not str(raw.get(field) or "").strip() for field in text_fields):
            raise ValueError("knowledge_expert_runtime_capability_binding_invalid")
        return cls(
            schema="ananta.knowledge-expert-runtime-capability.v1",
            provider_id=str(raw["provider_id"]),
            provider_version=str(raw["provider_version"]),
            base_model_digest=str(raw["base_model_digest"]),
            tokenizer_digest=str(raw["tokenizer_digest"]),
            architecture=str(raw["architecture"]),
            final_layer_name=str(raw["final_layer_name"]),
            supported_target_modules=tuple(str(item) for item in modules),
            dynamic_adapter_composition=bool(raw["dynamic_adapter_composition"]),
            token_entropy=bool(raw["token_entropy"]),
            kv_cache_safe_final_ffn=bool(raw["kv_cache_safe_final_ffn"]),
            atomic_expert_switch=bool(raw["atomic_expert_switch"]),
            max_active_experts=maximum,
        )

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "supported_target_modules": list(self.supported_target_modules)}


@dataclass(frozen=True, slots=True)
class KnowledgeExpertRoutingDecision:
    execute: bool
    mode: str
    reason_code: str
    candidate_manifest_digests: tuple[str, ...]
    entropy: float | None
    threshold: float
    generation_id: str
    requires_rag: bool

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "candidate_manifest_digests": list(self.candidate_manifest_digests)}


__all__ = ["KnowledgeExpertRoutingDecision", "KnowledgeExpertRuntimeCapability"]
