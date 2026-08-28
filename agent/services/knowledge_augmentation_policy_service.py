"""Canonical Hub policy for base, RAG and admitted expert augmentation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

_MODES = frozenset({"off", "auto", "rag_only", "expert_only", "expert_plus_rag", "base_only"})


@dataclass(frozen=True, slots=True)
class KnowledgeAugmentationDecision:
    mode: str
    use_expert: bool
    use_rag: bool
    reason_code: str
    policy_digest: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "ananta.knowledge-augmentation-decision.v1",
            "mode": self.mode,
            "use_expert": self.use_expert,
            "use_rag": self.use_rag,
            "reason_code": self.reason_code,
            "policy_digest": self.policy_digest,
        }


class KnowledgeAugmentationPolicyService:
    """Resolve only allowlisted profiles; callers cannot directly select an expert."""

    def __init__(self, *, profiles: Mapping[str, Mapping[str, Any]], policy_digest: str) -> None:
        self._profiles = {str(key): dict(value) for key, value in profiles.items()}
        self._policy_digest = policy_digest

    def decide(
        self,
        *,
        profile_id: str,
        global_enabled: bool,
        model_enabled: bool,
        task_enabled: bool,
        domain_enabled: bool,
        data_class_enabled: bool,
        runtime_ready: bool,
        expert_selected: bool,
        citation_required: bool,
        rag_available: bool,
    ) -> KnowledgeAugmentationDecision:
        profile = self._profiles.get(profile_id)
        if profile is None:
            return self._fallback("profile_unknown", rag_available)
        mode = str(profile.get("mode", "rag_only"))
        if mode not in _MODES:
            return self._fallback("profile_mode_invalid", rag_available)
        enabled = all((global_enabled, model_enabled, task_enabled, domain_enabled, data_class_enabled))
        if not enabled or mode == "off":
            return self._fallback("expert_disabled", rag_available)
        if mode == "base_only":
            return self._decision(mode, False, False, "base_only")
        if mode == "rag_only":
            return self._decision(mode, False, rag_available, "rag_only")
        if not runtime_ready or not expert_selected:
            return self._fallback("expert_unavailable", rag_available)
        if citation_required:
            if not rag_available:
                return self._decision("base_only", False, False, "citation_evidence_unavailable")
            return self._decision("expert_plus_rag", True, True, "citation_requires_rag")
        use_rag = mode in {"auto", "expert_plus_rag"} and rag_available
        resolved = "expert_plus_rag" if use_rag else "expert_only"
        return self._decision(resolved, True, use_rag, "expert_admitted")

    def _fallback(self, reason: str, rag_available: bool) -> KnowledgeAugmentationDecision:
        return self._decision("rag_only" if rag_available else "base_only", False, rag_available, reason)

    def _decision(self, mode: str, expert: bool, rag: bool, reason: str) -> KnowledgeAugmentationDecision:
        return KnowledgeAugmentationDecision(mode, expert, rag, reason, self._policy_digest)


__all__ = ["KnowledgeAugmentationDecision", "KnowledgeAugmentationPolicyService"]
