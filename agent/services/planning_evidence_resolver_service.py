from __future__ import annotations

import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import Any

from agent.services.citation_verification_service import CitationVerificationService
from agent.services.source_catalog_authority_service import (
    ResolvedSourceCatalog,
    SourceCatalogAuthorityService,
)

_SOURCE_ID = re.compile(r"^(?:SRC|RUN)_[0-9]{4}$")


class PlanningEvidenceError(ValueError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True, slots=True)
class AssignmentEvidenceContext:
    task_id: str
    assignment_id: str
    dispatch_lease_id: str
    tenant_id: str
    scope: str
    source_catalog_id: str
    source_catalog_hash: str
    allowed_source_refs: frozenset[str]
    allowed_run_refs: frozenset[str]
    artifact_hashes: Mapping[str, str]

    @property
    def allowed_refs(self) -> frozenset[str]:
        return self.allowed_source_refs | self.allowed_run_refs


class PlanningEvidenceResolverService:
    """Fail-closed adapter over Ananta's catalog and citation authorities."""

    def __init__(
        self,
        *,
        catalog_authority: SourceCatalogAuthorityService | None = None,
        citation_verifier: CitationVerificationService | None = None,
    ) -> None:
        self._catalog_authority = catalog_authority or SourceCatalogAuthorityService()
        self._citation_verifier = citation_verifier or CitationVerificationService()

    def resolve_catalog(self, **authority_arguments: Any) -> ResolvedSourceCatalog:
        """Delegate catalog ownership checks to the existing Hub authority."""
        return self._catalog_authority.resolve(**authority_arguments)

    def validate_runtime_binding(
        self,
        *,
        expected: AssignmentEvidenceContext,
        assignment_id: str,
        dispatch_lease_id: str,
        artifact_hashes: Mapping[str, str] | None = None,
    ) -> None:
        if str(assignment_id or "") != expected.assignment_id:
            raise PlanningEvidenceError("evidence_assignment_mismatch")
        if str(dispatch_lease_id or "") != expected.dispatch_lease_id:
            raise PlanningEvidenceError("evidence_dispatch_lease_mismatch")
        supplied_hashes = dict(artifact_hashes or {})
        for artifact_ref, expected_hash in expected.artifact_hashes.items():
            if str(supplied_hashes.get(artifact_ref) or "") != str(expected_hash or ""):
                raise PlanningEvidenceError("evidence_artifact_hash_mismatch")

    def validate_reference_allowlist(
        self,
        *,
        refs: Collection[str],
        context: AssignmentEvidenceContext,
    ) -> tuple[str, ...]:
        normalized = tuple(str(ref or "").strip() for ref in refs if str(ref or "").strip())
        for ref in normalized:
            if _SOURCE_ID.fullmatch(ref) is None:
                raise PlanningEvidenceError("evidence_reference_id_invalid")
            if ref not in context.allowed_refs:
                raise PlanningEvidenceError("evidence_reference_not_allowed")
        return normalized

    def verify_grounded_claims(
        self,
        *,
        answer_payload: dict[str, Any],
        source_catalog: Mapping[str, Any],
        tool_run_catalog: Collection[Mapping[str, Any]] | None,
        context: AssignmentEvidenceContext,
    ) -> dict[str, Any]:
        if str(source_catalog.get("source_catalog_id") or "") != context.source_catalog_id:
            return self._failed("source_catalog_id_mismatch")
        if str(source_catalog.get("source_catalog_hash") or "") != context.source_catalog_hash:
            return self._failed("source_catalog_hash_mismatch")

        catalog_sources = [dict(row) for row in list(source_catalog.get("sources") or []) if isinstance(row, Mapping)]
        run_sources = [dict(row) for row in list(tool_run_catalog or []) if isinstance(row, Mapping)]
        catalog_ids = {
            str(row.get("source_id") or "") for row in catalog_sources + run_sources if str(row.get("source_id") or "")
        }
        try:
            claim_refs = [
                str(ref)
                for claim in list(answer_payload.get("claims") or [])
                if isinstance(claim, Mapping)
                for ref in list(claim.get("citation_refs") or [])
            ]
            self.validate_reference_allowlist(refs=claim_refs, context=context)
        except PlanningEvidenceError as exc:
            return self._failed(exc.reason_code)
        if any(ref not in catalog_ids for ref in claim_refs):
            return self._failed("evidence_reference_unknown")

        result = self._citation_verifier.verify(
            task_id=context.task_id,
            answer_payload=answer_payload,
            source_catalog={**dict(source_catalog), "sources": catalog_sources},
            tool_run_catalog=run_sources,
            allowed_source_task_ids={
                str(row.get("task_id") or "") for row in catalog_sources + run_sources if str(row.get("task_id") or "")
            },
        )
        result["allowed_reference_count"] = len(context.allowed_refs)
        return result

    @staticmethod
    def _failed(reason_code: str) -> dict[str, Any]:
        return {
            "status": "failed",
            "reason": reason_code,
            "verified_claim_count": 0,
            "unverified_claim_count": 0,
            "failed_claims": [],
        }


__all__ = [
    "AssignmentEvidenceContext",
    "PlanningEvidenceError",
    "PlanningEvidenceResolverService",
]
