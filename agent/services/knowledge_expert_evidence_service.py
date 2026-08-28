"""Source-grounded evidence projection for active parametric experts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from ananta_contracts.parametric_knowledge import (
    KnowledgeExpertManifest,
    ParametricKnowledgeUnit,
    knowledge_unit_set_digest,
)


class CitationEvidencePort(Protocol):
    def resolve(self, *, source_ids: Sequence[str], query: str) -> Sequence[Mapping[str, Any]]: ...


class KnowledgeExpertEvidenceService:
    def __init__(self, *, citation_evidence: CitationEvidencePort) -> None:
        self._citation_evidence = citation_evidence

    def build(
        self,
        *,
        manifest: KnowledgeExpertManifest,
        units: Sequence[ParametricKnowledgeUnit],
        query: str,
        citation_required: bool,
    ) -> dict[str, Any]:
        allowed_units = {
            unit.unit_id: unit
            for unit in units
            if not unit.revoked
            and unit.unit_id in manifest.knowledge_unit_ids
            and (unit.tenant_id, unit.workspace_id, unit.repository_id)
            == (manifest.tenant_id, manifest.workspace_id, manifest.repository_id)
        }
        if set(manifest.knowledge_unit_ids).difference(allowed_units):
            raise ValueError("knowledge_expert_evidence_unit_unavailable")
        if knowledge_unit_set_digest(tuple(allowed_units.values())) != manifest.knowledge_unit_digest:
            raise ValueError("knowledge_expert_evidence_unit_digest_mismatch")
        source_ids = tuple(sorted({unit.source_id for unit in allowed_units.values()}))
        source_revisions = {(unit.source_id, unit.source_revision) for unit in allowed_units.values()}
        citations: list[dict[str, Any]] = []
        if citation_required:
            raw = self._citation_evidence.resolve(source_ids=source_ids, query=query)
            for item in raw:
                source_id = str(item.get("source_id") or "")
                revision = str(item.get("revision") or "")
                if source_id not in source_ids or (source_id, revision) not in source_revisions:
                    raise ValueError("knowledge_expert_evidence_unknown_source")
                citations.append({"source_id": source_id, "revision": revision})
            if not citations:
                raise ValueError("knowledge_expert_evidence_citation_missing")
        return {
            "schema": "ananta.parametric-answer-evidence.v1",
            "expert_manifest_digest": manifest.manifest_digest,
            "knowledge_unit_ids": sorted(allowed_units),
            "source_ids": list(source_ids),
            "citations": sorted(citations, key=lambda item: (item["source_id"], item["revision"])),
            "citation_required": citation_required,
        }


__all__ = ["CitationEvidencePort", "KnowledgeExpertEvidenceService"]
