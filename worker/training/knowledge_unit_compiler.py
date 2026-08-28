"""Worker-side deterministic CodeCompass record to KnowledgeUnit compiler."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence

from ananta_contracts.parametric_knowledge import ParametricKnowledgeUnit, canonical_sha256


class EligibilityDecisionPort(Protocol):
    def evaluate(
        self,
        unit: ParametricKnowledgeUnit,
        *,
        tenant_id: str,
        workspace_id: str,
        repository_id: str,
    ) -> Any: ...


@dataclass(frozen=True, slots=True)
class CompiledKnowledgeUnit:
    unit: ParametricKnowledgeUnit
    training_text: str
    compiler_digest: str
    eligibility: Mapping[str, Any]

    def to_dict(self, *, include_training_text: bool = False) -> dict[str, Any]:
        result = {
            "schema": "ananta.compiled-knowledge-unit.v1",
            "unit": self.unit.to_dict(),
            "compiler_digest": self.compiler_digest,
            "eligibility": dict(self.eligibility),
            "training_text_digest": hashlib.sha256(self.training_text.encode("utf-8")).hexdigest(),
        }
        if include_training_text:
            result["training_text"] = self.training_text
        return result


class CodeCompassKnowledgeUnitCompiler:
    def __init__(self, *, eligibility: EligibilityDecisionPort, compiler_version: str = "dmoe-unit-compiler.v1"):
        self._eligibility = eligibility
        self._compiler_version = str(compiler_version)

    def compile(
        self,
        records: Sequence[Mapping[str, Any]],
        *,
        tenant_id: str,
        workspace_id: str,
        repository_id: str,
        source_revision: str,
    ) -> dict[str, Any]:
        compiled: list[CompiledKnowledgeUnit] = []
        rejected: list[dict[str, Any]] = []
        for record in sorted(records, key=lambda item: str(item.get("record_id") or "")):
            try:
                candidate = self._compile_one(
                    record,
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    repository_id=repository_id,
                    source_revision=source_revision,
                )
                decision = self._eligibility.evaluate(
                    candidate.unit,
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    repository_id=repository_id,
                )
                decision_payload = decision.to_dict()
                candidate = CompiledKnowledgeUnit(
                    unit=candidate.unit,
                    training_text=candidate.training_text,
                    compiler_digest=candidate.compiler_digest,
                    eligibility=decision_payload,
                )
                if decision_payload.get("decision") == "allow":
                    compiled.append(candidate)
                else:
                    rejected.append(
                        {
                            "record_id": candidate.unit.unit_id,
                            "decision": decision_payload.get("decision"),
                            "reason_codes": list(decision_payload.get("reason_codes") or []),
                        }
                    )
            except (KeyError, TypeError, ValueError) as exc:
                known_reason = str(exc)
                if not known_reason.startswith(("knowledge_", "parametric_")):
                    known_reason = "knowledge_unit_compile_failed"
                rejected.append(
                    {
                        "record_id": str(record.get("record_id") or ""),
                        "decision": "deny",
                        "reason_codes": [known_reason or "knowledge_unit_compile_failed"],
                    }
                )
        manifest = [item.to_dict() for item in compiled]
        return {
            "schema": "ananta.knowledge-unit-compiler-result.v1",
            "compiler_version": self._compiler_version,
            "scope": {
                "tenant_id": tenant_id,
                "workspace_id": workspace_id,
                "repository_id": repository_id,
                "source_revision": source_revision,
            },
            "units": manifest,
            "rejected": rejected,
            "result_digest": canonical_sha256({"units": manifest, "rejected": rejected}),
        }

    def _compile_one(
        self,
        record: Mapping[str, Any],
        *,
        tenant_id: str,
        workspace_id: str,
        repository_id: str,
        source_revision: str,
    ) -> CompiledKnowledgeUnit:
        record_id = str(record.get("record_id") or "").strip()
        source_id = str(record.get("source_id") or "").strip()
        content_hash = str(record.get("content_hash") or record.get("document_hash") or "").strip()
        provenance = dict(record.get("provenance") or {})
        provenance_digest = str(record.get("provenance_digest") or "").strip()
        text_fields = dict(record.get("text_fields") or {})
        content = "\n".join(
            str(text_fields.get(field) or "").strip()
            for field in ("symbol_text", "summary_text", "content_text", "relation_text")
            if str(text_fields.get(field) or "").strip()
        )
        if not record_id or not source_id or len(content) < 8:
            raise ValueError("knowledge_unit_source_record_invalid")
        if not provenance_digest and provenance:
            provenance_digest = canonical_sha256(provenance)
        unit_payload = {
            "schema": "ananta.parametric-knowledge-unit.v1",
            "unit_id": record_id,
            "tenant_id": tenant_id,
            "workspace_id": workspace_id,
            "repository_id": repository_id,
            "source_id": source_id,
            "source_revision": source_revision,
            "content_hash": content_hash,
            "provenance_digest": provenance_digest,
            "domain": str(record.get("domain") or "unknown"),
            "parent_id": str(record.get("parent_id") or ""),
            "relations": sorted({str(item) for item in record.get("relations") or [] if str(item)}),
            "sensitivity": str(record.get("sensitivity") or "unknown"),
            "retention_until": str(record.get("retention_until") or ""),
            "license_spdx": str(record.get("license_spdx") or ""),
            "citation_ref": str(record.get("citation_ref") or ""),
            "citation_required": record.get("citation_required", True),
            "stable": record.get("stable", False),
            "approval_state": str(record.get("approval_state") or "unreviewed"),
            "revoked": record.get("revoked", False),
        }
        unit = ParametricKnowledgeUnit.from_mapping(unit_payload)
        compiler_digest = canonical_sha256(
            {
                "compiler_version": self._compiler_version,
                "unit_binding": unit.binding_digest,
                "training_text_digest": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            }
        )
        return CompiledKnowledgeUnit(unit, content, compiler_digest, {})


__all__ = ["CodeCompassKnowledgeUnitCompiler", "CompiledKnowledgeUnit", "EligibilityDecisionPort"]
