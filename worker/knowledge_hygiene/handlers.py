"""Bounded proposal handlers.  None can enqueue work or call another worker."""

from __future__ import annotations

from typing import Mapping, Sequence

from agent.services.knowledge_hygiene.analysis import AnalysisResult, analyze_claims
from agent.services.knowledge_hygiene.projection import GraphSupplement, materialize_graph_supplement
from ananta_contracts.knowledge_hygiene import (
    CoverageState,
    CuratedWikiPage,
    KnowledgeClaim,
    KnowledgeConflict,
    WorkerKnowledgeHygieneResult,
    canonical_digest,
)
from worker.knowledge_hygiene.contracts import (
    KnowledgeHygieneAssignment,
    KnowledgeHygieneWorkerError,
)


CLAIM_EXTRACTION_PROMPT_VERSION = "knowledge_claim_extraction.v1"
WIKI_SYNTHESIS_PROMPT_VERSION = "curated_wiki_synthesis.v1"
CORRECTION_PROMPT_VERSION = "knowledge_correction_proposal.v1"


class ClaimExtractionHandler:
    """Extracts only explicitly structured claims; free text is never guessed."""

    def handle(
        self,
        *,
        assignment: KnowledgeHygieneAssignment,
        records: Sequence[Mapping[str, object]],
        now: float,
        fixture_mode: bool = False,
    ) -> WorkerKnowledgeHygieneResult:
        assignment.require_operation("extract_claims", now=now)
        max_claims = int(assignment.budgets.get("claims", 0))
        claims: list[dict[str, object]] = []
        rejected = 0
        for record in records:
            source_id = str(record.get("source_id") or "")
            source_revision = str(record.get("source_revision") or "")
            locator = str(record.get("source_locator") or "")
            binding = assignment.binding_for(source_id, source_revision, locator)
            if str(record.get("content_sha256") or "") != binding.content_sha256:
                raise KnowledgeHygieneWorkerError("record_digest_mismatch")
            proposals = record.get("claims")
            if not isinstance(proposals, list):
                rejected += 1
                continue
            if not fixture_mode and record.get("proposal_source") != "validated_model_output":
                rejected += 1
                continue
            for raw in proposals:
                if not isinstance(raw, Mapping):
                    rejected += 1
                    continue
                if len(claims) >= max_claims:
                    return self._result(
                        assignment,
                        claims,
                        CoverageState.PARTIAL,
                        processed=len(records),
                        rejected=rejected,
                    )
                claims.append(
                    {
                        "project_id": assignment.project_id,
                        "subject": str(raw.get("subject") or ""),
                        "predicate": str(raw.get("predicate") or ""),
                        "value": raw.get("value"),
                        "unit": raw.get("unit"),
                        "status": raw.get("status"),
                        "assertion_kind": str(raw.get("assertion_kind") or "actual"),
                        "scope": str(raw.get("scope") or "project"),
                        "effective_from": raw.get("effective_from"),
                        "effective_to": raw.get("effective_to"),
                        "confidence": float(raw.get("confidence", 1.0)),
                        "supersedes_claim_refs": [
                            [str(item[0]), int(item[1])]
                            for item in raw.get("supersedes_claim_refs") or ()
                        ],
                        "source_id": source_id,
                        "source_revision": source_revision,
                        "source_locator": locator,
                        "source_content_sha256": binding.content_sha256,
                        "prompt_version": CLAIM_EXTRACTION_PROMPT_VERSION,
                    }
                )
        coverage = CoverageState.COMPLETE if rejected == 0 else CoverageState.PARTIAL
        return self._result(
            assignment,
            claims,
            coverage,
            processed=len(records),
            rejected=rejected,
        )

    @staticmethod
    def _result(
        assignment: KnowledgeHygieneAssignment,
        claims: Sequence[Mapping[str, object]],
        coverage: CoverageState,
        *,
        processed: int,
        rejected: int,
    ) -> WorkerKnowledgeHygieneResult:
        return WorkerKnowledgeHygieneResult(
            run_id=assignment.run_id,
            assignment_digest=assignment.assignment_digest,
            claims=tuple(claims),
            coverage=coverage,
            processed_records=processed,
            rejected_records=rejected,
        )


class ConflictAnalysisHandler:
    def handle(
        self,
        *,
        assignment: KnowledgeHygieneAssignment,
        claims: Sequence[KnowledgeClaim],
        now: float,
    ) -> AnalysisResult:
        assignment.require_operation("analyze_candidates", now=now)
        if any(claim.project_id != assignment.project_id for claim in claims):
            raise KnowledgeHygieneWorkerError("cross_project_claim_rejected")
        return analyze_claims(
            claims,
            max_candidate_pairs=int(assignment.budgets.get("candidate_pairs", 0)),
            now=now,
        )


class WikiSynthesisHandler:
    def handle(
        self,
        *,
        assignment: KnowledgeHygieneAssignment,
        title: str,
        slug: str,
        body_markdown: str,
        claims: Sequence[KnowledgeClaim],
        conflicts: Sequence[KnowledgeConflict],
        aliases: Sequence[str],
        now: float,
    ) -> dict[str, object]:
        assignment.require_operation("synthesize_wiki", now=now)
        if len(claims) > int(assignment.budgets.get("claims", 0)):
            raise KnowledgeHygieneWorkerError("wiki_claim_budget_exceeded")
        if any(item.project_id != assignment.project_id for item in claims):
            raise KnowledgeHygieneWorkerError("cross_project_claim_rejected")
        claim_ids = {item.claim_id for item in claims}
        relevant = sorted(
            item.conflict_id
            for item in conflicts
            if {item.left_claim_id, item.right_claim_id} & claim_ids
        )
        return {
            "schema": "curated_wiki_page.v1",
            "project_id": assignment.project_id,
            "title": title,
            "slug": slug,
            "body_markdown": body_markdown,
            "claim_refs": [[item.claim_id, item.revision] for item in claims],
            "conflict_refs": relevant,
            "aliases": sorted(set(aliases)),
            "evidence_marking": "claim_refs",
            "inference_marking": "explicit_required",
            "prompt_version": WIKI_SYNTHESIS_PROMPT_VERSION,
        }


class CorrectionProposalHandler:
    def handle(
        self,
        *,
        assignment: KnowledgeHygieneAssignment,
        conflict: KnowledgeConflict,
        source_id: str,
        source_revision: str,
        source_locator: str,
        base_content_sha256: str,
        proposed_content: str,
        now: float,
    ) -> dict[str, object]:
        assignment.require_operation("propose_correction", now=now)
        binding = assignment.binding_for(source_id, source_revision, source_locator)
        if binding.content_sha256 != base_content_sha256:
            raise KnowledgeHygieneWorkerError("correction_base_digest_mismatch")
        if conflict.project_id != assignment.project_id:
            raise KnowledgeHygieneWorkerError("cross_project_conflict_rejected")
        return {
            "project_id": assignment.project_id,
            "conflict_id": conflict.conflict_id,
            "source_id": source_id,
            "source_revision": source_revision,
            "source_locator": source_locator,
            "base_content_sha256": base_content_sha256,
            "proposed_content": proposed_content,
            "prompt_version": CORRECTION_PROMPT_VERSION,
            "proposal_basis_digest": canonical_digest(
                {
                    "assignment_digest": assignment.assignment_digest,
                    "conflict_basis_digest": conflict.basis_digest,
                    "source_id": source_id,
                    "source_revision": source_revision,
                    "source_locator": source_locator,
                    "base_content_sha256": base_content_sha256,
                }
            ),
        }


class GraphSupplementHandler:
    def handle(
        self,
        *,
        assignment: KnowledgeHygieneAssignment,
        claims: Sequence[KnowledgeClaim],
        conflicts: Sequence[KnowledgeConflict],
        pages: Sequence[CuratedWikiPage],
        now: float,
    ) -> GraphSupplement:
        assignment.require_operation("materialize_graph", now=now)
        if any(item.project_id != assignment.project_id for item in (*claims, *conflicts, *pages)):
            raise KnowledgeHygieneWorkerError("cross_project_graph_input_rejected")
        return materialize_graph_supplement(
            project_id=assignment.project_id,
            claims=claims,
            conflicts=conflicts,
            pages=pages,
        )
