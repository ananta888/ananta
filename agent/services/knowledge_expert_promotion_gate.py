"""Hub promotion gate for single, pairwise and multi-expert evaluations."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ananta_contracts.parametric_knowledge import KnowledgeExpertBank, KnowledgeExpertManifest


class KnowledgeExpertPromotionGate:
    def evaluate(
        self,
        *,
        bank: KnowledgeExpertBank,
        manifests: Sequence[KnowledgeExpertManifest],
        evidence: Mapping[str, Any],
    ) -> None:
        if evidence.get("schema") != "ananta.knowledge-expert-promotion-evidence.v1":
            raise ValueError("knowledge_expert_promotion_evidence_invalid")
        if evidence.get("bank_digest") != bank.bank_digest:
            raise ValueError("knowledge_expert_promotion_bank_binding_mismatch")
        evaluated = {
            tuple(sorted(str(item) for item in row.get("manifest_digests") or ()))
            for row in evidence.get("compositions") or ()
            if isinstance(row, Mapping) and row.get("passed") is True
        }
        required: set[tuple[str, ...]] = {(manifest.manifest_digest,) for manifest in manifests}
        if len(manifests) > 1:
            required.update(
                tuple(sorted((left.manifest_digest, right.manifest_digest)))
                for index, left in enumerate(manifests)
                for right in manifests[index + 1 :]
            )
            required.add(tuple(sorted(manifest.manifest_digest for manifest in manifests)))
        if not required.issubset(evaluated):
            raise ValueError("knowledge_expert_composition_evaluation_missing")
        if evidence.get("general_holdout_passed") is not True or evidence.get("security_holdout_passed") is not True:
            raise ValueError("knowledge_expert_promotion_holdout_failed")


__all__ = ["KnowledgeExpertPromotionGate"]
