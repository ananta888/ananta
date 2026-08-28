"""Worker projection of admitted manifests into CodeCompass index records."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ananta_contracts.parametric_knowledge import KnowledgeExpertBank, KnowledgeExpertManifest


class KnowledgeExpertIndexProjector:
    def project(
        self,
        *,
        bank: KnowledgeExpertBank,
        manifests: Sequence[KnowledgeExpertManifest],
        text_surrogates: Mapping[str, str],
    ) -> list[dict[str, Any]]:
        if bank.status not in {"admitted", "active"}:
            raise ValueError("knowledge_expert_index_bank_not_admitted")
        by_digest = {manifest.manifest_digest: manifest for manifest in manifests}
        if set(by_digest) != set(bank.expert_manifest_digests) or set(text_surrogates) != set(by_digest):
            raise ValueError("knowledge_expert_index_manifest_binding_mismatch")
        records: list[dict[str, Any]] = []
        for digest in sorted(by_digest):
            manifest = by_digest[digest]
            if (manifest.tenant_id, manifest.workspace_id, manifest.repository_id) != (
                bank.tenant_id,
                bank.workspace_id,
                bank.repository_id,
            ):
                raise ValueError("knowledge_expert_index_scope_mismatch")
            surrogate = " ".join(str(text_surrogates[digest]).split())[:8000]
            if not surrogate:
                raise ValueError("knowledge_expert_index_surrogate_required")
            records.append(
                {
                    "id": f"expert-manifest:{digest}",
                    "kind": "knowledge_expert_manifest",
                    "summary": surrogate,
                    "source_id": manifest.expert_id,
                    "tenant_id": manifest.tenant_id,
                    "workspace_id": manifest.workspace_id,
                    "repository_id": manifest.repository_id,
                    "revision": manifest.generation_id,
                    "manifest_digest": digest,
                }
            )
        return records


__all__ = ["KnowledgeExpertIndexProjector"]
