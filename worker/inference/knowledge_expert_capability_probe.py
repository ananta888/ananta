"""Evidence-based runtime capability probe without provider-name heuristics."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from ananta_contracts.knowledge_expert_runtime import KnowledgeExpertRuntimeCapability


class KnowledgeExpertRuntimeIntrospectionPort(Protocol):
    def inspect(self) -> Mapping[str, Any]: ...


class KnowledgeExpertCapabilityProbe:
    def __init__(self, introspection: KnowledgeExpertRuntimeIntrospectionPort) -> None:
        self._introspection = introspection

    def probe(self) -> KnowledgeExpertRuntimeCapability:
        evidence = dict(self._introspection.inspect())
        required_proofs = {
            "final_ffn_probe_passed",
            "entropy_probe_passed",
            "composition_probe_passed",
            "kv_cache_probe_passed",
            "atomic_switch_probe_passed",
        }
        if set(evidence.get("proofs") or ()) != required_proofs:
            raise ValueError("knowledge_expert_runtime_proof_set_invalid")
        proofs = dict(evidence.get("proof_results") or {})
        if set(proofs) != required_proofs or any(proofs[name] is not True for name in required_proofs):
            raise ValueError("knowledge_expert_runtime_probe_failed")
        capability = evidence.get("capability")
        if not isinstance(capability, Mapping):
            raise ValueError("knowledge_expert_runtime_capability_missing")
        parsed = KnowledgeExpertRuntimeCapability.from_mapping(capability)
        if not (
            parsed.dynamic_adapter_composition
            and parsed.token_entropy
            and parsed.kv_cache_safe_final_ffn
            and parsed.atomic_expert_switch
        ):
            raise ValueError("knowledge_expert_runtime_probe_failed")
        return parsed


__all__ = ["KnowledgeExpertCapabilityProbe", "KnowledgeExpertRuntimeIntrospectionPort"]
