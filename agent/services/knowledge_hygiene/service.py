"""Hub application service for the Knowledge Hygiene lifecycle."""

from __future__ import annotations

import re
import time
from dataclasses import asdict, replace
from typing import Callable, Mapping, Sequence

from agent.repositories.knowledge_hygiene_repository import (
    AuditRecord,
    KnowledgeHygieneRepository,
    Page,
    new_audit_record,
)
from agent.services.knowledge_hygiene.analysis import AnalysisResult, analyze_claims
from agent.services.knowledge_hygiene.config import KnowledgeHygieneConfig
from agent.services.knowledge_hygiene.projection import (
    AtomicMarkdownProjector,
    GraphSupplement,
    materialize_graph_supplement,
)
from agent.services.knowledge_hygiene.run_service import KnowledgeHygieneRunService
from agent.services.knowledge_hygiene.writeback import (
    KnowledgeWritebackPort,
    WritebackReceipt,
    content_sha256,
)
from ananta_contracts.knowledge_claim_precedence import compare_claims
from ananta_contracts.knowledge_hygiene import (
    ConflictState,
    CorrectionProposal,
    CoverageState,
    CuratedWikiPage,
    DecisionKind,
    KnowledgeClaim,
    KnowledgeConflict,
    KnowledgeConflictDecision,
    KnowledgeHealthSnapshot,
    KnowledgeHygieneRun,
    RunState,
    SourceRevisionBinding,
    WorkerKnowledgeHygieneResult,
    build_correction_proposal,
    canonical_digest,
)


_SAFE_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_AUDIT_SECRET_KEYS = frozenset(
    {"content", "body_markdown", "proposed_content", "token", "secret", "password", "authorization"}
)


class KnowledgeHygieneServiceError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class KnowledgeHygieneService:
    """Coordinates repositories and pure policies; it never delegates from a worker."""

    def __init__(
        self,
        repository: KnowledgeHygieneRepository,
        config: KnowledgeHygieneConfig,
        *,
        clock: Callable[[], float] = time.time,
        projector: AtomicMarkdownProjector | None = None,
        writeback: KnowledgeWritebackPort | None = None,
    ) -> None:
        self.repository = repository
        self.config = config
        self.clock = clock
        self.runs = KnowledgeHygieneRunService(repository, clock=clock)
        self.projector = projector or AtomicMarkdownProjector(config.projection_dir)
        self.writeback = writeback

    def ensure_available(self) -> None:
        self._require_enabled()

    def start_run(
        self,
        *,
        run_id: str,
        project_id: str,
        source_bindings: Sequence[SourceRevisionBinding],
        actor_id: str,
        profile_name: str = "deterministic-v1",
        policy_version: str = "knowledge_claim_precedence.v1",
        budgets: Mapping[str, int] | None = None,
        automatic: bool = False,
    ) -> KnowledgeHygieneRun:
        self._require_enabled()
        if automatic and not self.config.auto_run_enabled:
            raise KnowledgeHygieneServiceError("knowledge_hygiene_auto_run_disabled")
        effective_budgets = {
            "claims": self.config.max_claims_per_run,
            "candidate_pairs": self.config.max_candidate_pairs,
            "pages": self.config.max_pages_per_run,
            **dict(budgets or {}),
        }
        if effective_budgets["claims"] > self.config.max_claims_per_run:
            raise KnowledgeHygieneServiceError("claim_budget_exceeds_policy")
        if effective_budgets["candidate_pairs"] > self.config.max_candidate_pairs:
            raise KnowledgeHygieneServiceError("candidate_budget_exceeds_policy")
        return self.runs.create(
            run_id=run_id,
            project_id=project_id,
            source_bindings=source_bindings,
            policy_version=policy_version,
            profile_name=profile_name,
            budgets=effective_budgets,
            actor_id=actor_id,
        )

    def admit_worker_result(
        self,
        *,
        project_id: str,
        worker_id: str,
        result: WorkerKnowledgeHygieneResult,
    ) -> tuple[KnowledgeHygieneRun, tuple[KnowledgeClaim, ...]]:
        self._require_enabled()
        run = self._require_run(project_id, result.run_id)
        if run.assignment_digest != result.assignment_digest:
            raise KnowledgeHygieneServiceError("assignment_digest_mismatch")
        if run.result_digest is not None:
            if run.result_digest == result.result_digest:
                claims = tuple(
                    item for item in self._all_claims(project_id)
                    if item.extraction_run_id == run.run_id
                )
                return run, claims
            raise KnowledgeHygieneServiceError("result_replay_mismatch")
        if run.lease_owner != worker_id or run.state not in {RunState.DISPATCHED, RunState.RUNNING}:
            raise KnowledgeHygieneServiceError("worker_not_bound_to_run")
        if run.lease_expires_at is None or run.lease_expires_at < self.clock():
            raise KnowledgeHygieneServiceError("run_lease_expired")
        if len(result.claims) > min(int(run.budgets.get("claims", 0)), self.config.max_claims_per_run):
            raise KnowledgeHygieneServiceError("worker_claim_budget_exceeded")
        if len(result.wiki_pages) > min(int(run.budgets.get("pages", 0)), self.config.max_pages_per_run):
            raise KnowledgeHygieneServiceError("worker_page_budget_exceeded")
        bindings = {(item.source_id, item.source_revision): item for item in run.source_bindings}
        admitted: list[KnowledgeClaim] = []
        for proposal in result.claims:
            source_id = str(proposal.get("source_id") or "")
            source_revision = str(proposal.get("source_revision") or "")
            binding = bindings.get((source_id, source_revision))
            if binding is None:
                raise KnowledgeHygieneServiceError("claim_source_outside_assignment")
            locator = str(proposal.get("source_locator") or "")
            if locator not in binding.allowed_locators:
                raise KnowledgeHygieneServiceError("claim_locator_outside_assignment")
            if str(proposal.get("source_content_sha256") or "") != binding.content_sha256:
                raise KnowledgeHygieneServiceError("claim_source_digest_mismatch")
            if str(proposal.get("project_id") or project_id) != project_id:
                raise KnowledgeHygieneServiceError("cross_project_claim_rejected")
            identity = canonical_digest(
                {
                    "project_id": project_id,
                    "source_id": source_id,
                    "locator": locator,
                    "subject": str(proposal.get("subject") or "").casefold().strip(),
                    "predicate": str(proposal.get("predicate") or "").casefold().strip(),
                    "scope": str(proposal.get("scope") or "project"),
                }
            )
            claim_id = f"KCL_{identity[:24]}"
            previous = self.repository.get_claim(project_id, claim_id)
            revision = 1 if previous is None else previous.revision + 1
            candidate = KnowledgeClaim(
                claim_id=claim_id,
                project_id=project_id,
                revision=revision,
                subject=str(proposal.get("subject") or ""),
                predicate=str(proposal.get("predicate") or ""),
                value=proposal.get("value"),
                source_id=source_id,
                source_revision=source_revision,
                source_locator=locator,
                source_content_sha256=binding.content_sha256,
                extraction_run_id=run.run_id,
                scope=str(proposal.get("scope") or "project"),
                unit=str(proposal["unit"]) if proposal.get("unit") is not None else None,
                status=str(proposal["status"]) if proposal.get("status") is not None else None,
                assertion_kind=str(proposal.get("assertion_kind") or "actual"),
                effective_from=str(proposal["effective_from"]) if proposal.get("effective_from") else None,
                effective_to=str(proposal["effective_to"]) if proposal.get("effective_to") else None,
                confidence=float(proposal.get("confidence", 1.0)),
                coverage=CoverageState(result.coverage),
                created_at=self.clock(),
                supersedes_claim_refs=tuple(
                    (str(item[0]), int(item[1]))
                    for item in proposal.get("supersedes_claim_refs") or ()
                ),
            )
            if previous is not None and previous.idempotency_key == candidate.idempotency_key:
                admitted.append(previous)
            else:
                admitted.append(self.repository.put_claim(candidate))
        for page_proposal in result.wiki_pages:
            self.publish_page(
                project_id=project_id,
                proposal=page_proposal,
                actor_id=worker_id,
            )
        finished = self.runs.finish(
            project_id=project_id,
            run_id=run.run_id,
            assignment_digest=result.assignment_digest,
            result_digest=result.result_digest,
            coverage=CoverageState(result.coverage),
        )
        self._audit(
            project_id=project_id,
            aggregate_type="run",
            aggregate_id=run.run_id,
            event_type="worker_result_admitted",
            actor_id=worker_id,
            payload={
                "result_digest": result.result_digest,
                "coverage": CoverageState(result.coverage).value,
                "processed_records": result.processed_records,
                "rejected_records": result.rejected_records,
                "admitted_claims": len(admitted),
                "admitted_wiki_pages": len(result.wiki_pages),
                "untrusted_conflict_candidates": len(result.conflict_candidates),
                "untrusted_correction_proposals": len(result.correction_proposals),
            },
        )
        return finished, tuple(admitted)

    def analyze_project(self, project_id: str, *, actor_id: str) -> AnalysisResult:
        self._require_enabled()
        claims = self._all_claims(project_id)
        result = analyze_claims(
            claims,
            max_candidate_pairs=self.config.max_candidate_pairs,
            now=self.clock(),
            semantic_threshold=self.config.semantic_similarity_threshold,
        )
        for conflict in result.conflicts:
            self.repository.put_conflict(conflict)
        self._audit(
            project_id=project_id,
            aggregate_type="project",
            aggregate_id=project_id,
            event_type="analysis_completed",
            actor_id=actor_id,
            payload={
                "deterministic_conflicts": len(result.conflicts),
                "exact_duplicates": len(result.exact_duplicates),
                "semantic_duplicates": len(result.semantic_duplicates),
                "evaluated_pairs": result.evaluated_pairs,
                "skipped_pairs": result.skipped_pairs,
                "coverage": result.coverage.value,
                "reason_codes": result.reason_codes,
            },
        )
        self.refresh_health(project_id, coverage=result.coverage)
        return result

    def publish_page(self, *, project_id: str, proposal: Mapping[str, object], actor_id: str) -> CuratedWikiPage:
        self._require_enabled()
        slug = str(proposal.get("slug") or "")
        if not _SAFE_SLUG.fullmatch(slug):
            raise KnowledgeHygieneServiceError("invalid_wiki_slug")
        raw_refs = proposal.get("claim_refs") or ()
        claim_refs = tuple((str(item[0]), int(item[1])) for item in raw_refs)
        if not claim_refs or len(claim_refs) > self.config.max_claims_per_run:
            raise KnowledgeHygieneServiceError("invalid_wiki_claim_refs")
        claims: list[KnowledgeClaim] = []
        for claim_id, revision in claim_refs:
            claim = self.repository.get_claim(project_id, claim_id, revision)
            if claim is None:
                raise KnowledgeHygieneServiceError("wiki_claim_not_found")
            claims.append(claim)
        conflicts = self._all_conflicts(project_id)
        relevant_conflicts = {
            item.conflict_id
            for item in conflicts
            if item.state in {ConflictState.OPEN, ConflictState.REOPENED, ConflictState.PENDING_REINGEST}
            and ({item.left_claim_id, item.right_claim_id} & {claim_id for claim_id, _ in claim_refs})
        }
        requested_conflicts = {str(item) for item in proposal.get("conflict_refs") or ()}
        if not relevant_conflicts.issubset(requested_conflicts):
            raise KnowledgeHygieneServiceError("wiki_conflict_warning_removed")
        current = self.repository.get_page(project_id, slug)
        revision = 1 if current is None else current.revision + 1
        page_id = current.page_id if current else f"KWP_{canonical_digest({'project_id': project_id, 'slug': slug})[:24]}"
        coverage = CoverageState.COMPLETE
        if any(item.coverage is CoverageState.UNKNOWN for item in claims):
            coverage = CoverageState.UNKNOWN
        elif any(item.coverage is CoverageState.PARTIAL for item in claims):
            coverage = CoverageState.PARTIAL
        page = CuratedWikiPage(
            page_id=page_id,
            project_id=project_id,
            slug=slug,
            title=str(proposal.get("title") or slug.replace("-", " ").title()),
            revision=revision,
            body_markdown=str(proposal.get("body_markdown") or ""),
            claim_refs=claim_refs,
            conflict_refs=tuple(sorted(requested_conflicts | relevant_conflicts)),
            source_refs=tuple(sorted({item.source_id for item in claims})),
            aliases=tuple(sorted({str(item) for item in proposal.get("aliases") or ()})),
            coverage=coverage,
            created_at=self.clock(),
        )
        if current is not None and current.content_hash == page.content_hash:
            return current
        persisted = self.repository.put_page(page)
        self.projector.project(persisted)
        self._audit(
            project_id=project_id,
            aggregate_type="wiki_page",
            aggregate_id=persisted.page_id,
            event_type="wiki_revision_published",
            actor_id=actor_id,
            payload={"slug": slug, "revision": persisted.revision, "content_hash": persisted.content_hash},
        )
        return persisted

    def decide_conflict(
        self,
        *,
        project_id: str,
        conflict_id: str,
        decision_id: str,
        expected_version: int,
        basis_digest: str,
        actor_id: str,
        actor_kind: str,
        decision: str,
        rationale: str,
        qualifiers: Sequence[str] = (),
        writeback_requested: bool = False,
        second_approver_id: str | None = None,
    ) -> KnowledgeConflict:
        self._require_manual()
        if actor_kind != "human":
            raise KnowledgeHygieneServiceError("human_actor_required")
        current = self._require_conflict(project_id, conflict_id)
        if self.config.require_dual_approval and (
            not second_approver_id or second_approver_id == actor_id
        ):
            raise KnowledgeHygieneServiceError("independent_second_approver_required")
        try:
            kind = DecisionKind(decision)
        except ValueError as exc:
            raise KnowledgeHygieneServiceError("invalid_decision") from exc
        record = KnowledgeConflictDecision(
            decision_id=decision_id,
            conflict_id=conflict_id,
            project_id=project_id,
            expected_conflict_version=expected_version,
            actor_id=actor_id,
            actor_kind="human",
            decision=kind,
            rationale=rationale,
            qualifiers=tuple(qualifiers),
            basis_digest=basis_digest,
            created_at=self.clock(),
            writeback_requested=writeback_requested,
            second_approver_id=second_approver_id,
        )
        next_state = (
            ConflictState.RESOLVED
            if kind in {DecisionKind.KEEP_BOTH, DecisionKind.DISMISS_NOT_CONFLICT}
            else ConflictState.PENDING_REINGEST
        )
        updated = replace(
            current,
            state=next_state,
            version=current.version + 1,
            updated_at=self.clock(),
        )
        persisted = self.repository.decide_conflict(updated, record)
        if persisted != current:
            self._audit(
                project_id=project_id,
                aggregate_type="conflict",
                aggregate_id=conflict_id,
                event_type="human_decision_recorded",
                actor_id=actor_id,
                payload={
                    "decision_id": decision_id,
                    "decision": kind.value,
                    "basis_digest": basis_digest,
                    "qualifier_count": len(qualifiers),
                    "writeback_requested": writeback_requested,
                },
            )
        return persisted

    def propose_correction(
        self,
        *,
        project_id: str,
        conflict_id: str,
        run_id: str,
        correction_id: str,
        source_id: str,
        source_revision: str,
        source_locator: str,
        base_content_sha256: str,
        proposed_content: str,
        worker_id: str,
    ) -> CorrectionProposal:
        self._require_enabled()
        self._require_conflict(project_id, conflict_id)
        run = self._require_run(project_id, run_id)
        now = self.clock()
        if (
            run.state not in {RunState.DISPATCHED, RunState.RUNNING}
            or run.lease_owner != worker_id
            or run.lease_expires_at is None
            or run.lease_expires_at < now
        ):
            raise KnowledgeHygieneServiceError("correction_worker_not_bound")
        binding = next(
            (
                item for item in run.source_bindings
                if item.source_id == source_id and item.source_revision == source_revision
            ),
            None,
        )
        if binding is None or source_locator not in binding.allowed_locators:
            raise KnowledgeHygieneServiceError("correction_source_outside_assignment")
        if binding.content_sha256 != base_content_sha256:
            raise KnowledgeHygieneServiceError("correction_base_digest_mismatch")
        if len(proposed_content.encode("utf-8")) > self.config.max_patch_bytes:
            raise KnowledgeHygieneServiceError("correction_too_large")
        proposal = build_correction_proposal(
            correction_id=correction_id,
            project_id=project_id,
            conflict_id=conflict_id,
            source_id=source_id,
            source_revision=source_revision,
            source_locator=source_locator,
            base_content_sha256=base_content_sha256,
            proposed_content=proposed_content,
            proposed_by_run_id=run_id,
            created_at=now,
        )
        persisted = self.repository.put_correction(proposal)
        self._audit(
            project_id=project_id,
            aggregate_type="correction",
            aggregate_id=correction_id,
            event_type="correction_proposed",
            actor_id=worker_id,
            payload={
                "conflict_id": conflict_id,
                "source_id": source_id,
                "source_revision": source_revision,
                "source_locator": source_locator,
                "base_content_sha256": base_content_sha256,
                "proposal_digest": proposal.proposal_digest,
            },
        )
        return persisted

    def approve_writeback(
        self,
        *,
        project_id: str,
        correction_id: str,
        proposal_digest: str,
        actor_id: str,
        actor_kind: str,
    ) -> WritebackReceipt:
        self._require_manual()
        if actor_kind != "human":
            raise KnowledgeHygieneServiceError("human_actor_required")
        if not self.config.source_writeback_enabled:
            raise KnowledgeHygieneServiceError("source_writeback_disabled")
        if self.writeback is None:
            raise KnowledgeHygieneServiceError("writeback_adapter_unavailable")
        record = self.repository.get_correction(project_id, correction_id)
        if record is None:
            raise KnowledgeHygieneServiceError("correction_not_found")
        proposal, state = record
        if state != "proposed" or proposal.proposal_digest != proposal_digest:
            raise KnowledgeHygieneServiceError("stale_correction_proposal")
        receipt = self.writeback.apply(proposal)
        approved = replace(
            proposal,
            writeback_approved_by=actor_id,
            writeback_approved_at=self.clock(),
        )
        self.repository.update_correction(approved, expected_state="proposed", state="written")
        self._audit(
            project_id=project_id,
            aggregate_type="correction",
            aggregate_id=correction_id,
            event_type="writeback_completed",
            actor_id=actor_id,
            payload={
                "proposal_digest": proposal_digest,
                "previous_sha256": receipt.previous_sha256,
                "resulting_sha256": receipt.resulting_sha256,
            },
        )
        return receipt

    def correction_detail(self, project_id: str, correction_id: str) -> dict[str, object]:
        record = self.repository.get_correction(project_id, correction_id)
        if record is None:
            raise KnowledgeHygieneServiceError("correction_not_found")
        proposal, state = record
        preview = self.writeback.preview(proposal) if self.writeback is not None else {
            "status": "writeback_unavailable",
            "base_sha256": proposal.base_content_sha256,
            "current_sha256": "unavailable",
            "proposed_sha256": content_sha256(proposal.proposed_content),
            "diff": "",
        }
        return {"proposal": proposal.to_dict(), "state": state, "three_way": preview}

    def recheck_conflict(
        self,
        *,
        project_id: str,
        conflict_id: str,
        run_id: str,
        left_claim_id: str,
        right_claim_id: str,
        actor_id: str,
    ) -> KnowledgeConflict:
        self._require_enabled()
        conflict = self._require_conflict(project_id, conflict_id)
        run = self._require_run(project_id, run_id)
        if run.state is not RunState.COMPLETED or run.coverage is not CoverageState.COMPLETE:
            raise KnowledgeHygieneServiceError("complete_recheck_evidence_required")
        left = self.repository.get_claim(project_id, left_claim_id)
        right = self.repository.get_claim(project_id, right_claim_id)
        if left is None or right is None:
            raise KnowledgeHygieneServiceError("recheck_claim_not_found")
        if left.extraction_run_id != run_id or right.extraction_run_id != run_id:
            raise KnowledgeHygieneServiceError("recheck_claim_outside_run")
        comparison = compare_claims(left, right)
        state = ConflictState.REOPENED if comparison.candidate_conflict else ConflictState.RESOLVED
        updated = replace(
            conflict,
            left_claim_id=left.claim_id,
            left_claim_revision=left.revision,
            left_claim_digest=left.record_digest,
            right_claim_id=right.claim_id,
            right_claim_revision=right.revision,
            right_claim_digest=right.record_digest,
            coverage=CoverageState.COMPLETE,
            state=state,
            version=conflict.version + 1,
            updated_at=self.clock(),
            resolved_by_run_id=run_id if state is ConflictState.RESOLVED else None,
        )
        persisted = self.repository.update_conflict(updated, expected_version=conflict.version)
        self._audit(
            project_id=project_id,
            aggregate_type="conflict",
            aggregate_id=conflict_id,
            event_type="conflict_rechecked",
            actor_id=actor_id,
            payload={"run_id": run_id, "state": state.value, "reason_codes": comparison.reason_codes},
        )
        return persisted

    def refresh_health(self, project_id: str, *, coverage: CoverageState) -> KnowledgeHealthSnapshot:
        claims = self._all_claims(project_id)
        conflicts = self._all_conflicts(project_id)
        pages = self._all_pages(project_id)
        now = self.clock()
        referenced_claim_ids = {
            claim_id
            for page in pages
            for claim_id, _revision in page.claim_refs
        }
        open_conflicts = [
            item for item in conflicts
            if item.state in {ConflictState.OPEN, ConflictState.REOPENED, ConflictState.PENDING_REINGEST}
        ]
        canonical_counts: dict[str, int | None]
        if coverage is CoverageState.COMPLETE:
            canonical_counts = {
                "claims": len(claims),
                "conflicts": len(conflicts),
                "open_conflicts": len(open_conflicts),
                "wiki_pages": len(pages),
            }
        else:
            canonical_counts = {
                "claims": None,
                "conflicts": None,
                "open_conflicts": None,
                "wiki_pages": None,
            }
        counts = {
            **canonical_counts,
            "claims_observed": len(claims),
            "conflicts_observed": len(conflicts),
            "open_conflicts_observed": len(open_conflicts),
            "wiki_pages_observed": len(pages),
            "orphan_claims_observed": sum(
                1 for claim in claims
                if claim.claim_id not in referenced_claim_ids
            ),
            "superseded_claims_observed": sum(bool(claim.supersedes_claim_refs) for claim in claims),
        }
        previous = self.repository.latest_health(project_id)
        trend: dict[str, float | None] = {
            "open_conflicts_delta": None,
            "orphan_claims_delta": None,
        }
        if previous is not None and previous.scope_version == "project.v1" and previous.coverage is coverage:
            trend = {
                "open_conflicts_delta": _delta(counts.get("open_conflicts"), previous.counts.get("open_conflicts")),
                "orphan_claims_delta": _delta(
                    counts.get("orphan_claims_observed"),
                    previous.counts.get("orphan_claims_observed"),
                ),
            }
        oldest = max((now - item.created_at for item in open_conflicts), default=None)
        basis_digest = canonical_digest(
            {
                "project_id": project_id,
                "coverage": coverage.value,
                "claims": [[item.claim_id, item.revision, item.record_digest] for item in claims],
                "conflicts": [[item.conflict_id, item.version, item.state.value] for item in conflicts],
                "pages": [[item.page_id, item.revision, item.content_hash] for item in pages],
            }
        )
        snapshot = KnowledgeHealthSnapshot(
            snapshot_id=f"KHS_{basis_digest[:24]}",
            project_id=project_id,
            as_of=now,
            scope_version="project.v1",
            coverage=coverage,
            counts=counts,
            oldest_open_age_seconds=oldest,
            trend=trend,
            basis_digest=basis_digest,
        )
        return self.repository.put_health(snapshot)

    def graph_supplement(self, project_id: str) -> GraphSupplement:
        return materialize_graph_supplement(
            project_id=project_id,
            claims=self._all_claims(project_id),
            conflicts=self._all_conflicts(project_id),
            pages=self._all_pages(project_id),
        )

    def admit_graph_supplement(self, project_id: str, candidate: GraphSupplement) -> GraphSupplement:
        self._require_enabled()
        if candidate.project_id != project_id or candidate.version != "knowledge_graph_supplement.v1":
            raise KnowledgeHygieneServiceError("graph_supplement_scope_mismatch")
        canonical = self.graph_supplement(project_id)
        if (
            candidate.basis_hash != canonical.basis_hash
            or candidate.supplement_hash != canonical.supplement_hash
            or candidate.nodes != canonical.nodes
            or candidate.edges != canonical.edges
        ):
            raise KnowledgeHygieneServiceError("graph_supplement_digest_mismatch")
        return canonical

    def list_claims(self, project_id: str, *, cursor: str | None, limit: int) -> Page[KnowledgeClaim]:
        return self.repository.list_claims(project_id, cursor=cursor, limit=limit)

    def list_conflicts(self, project_id: str, *, state: str | None, cursor: str | None, limit: int) -> Page[KnowledgeConflict]:
        return self.repository.list_conflicts(project_id, state=state, cursor=cursor, limit=limit)

    def list_pages(self, project_id: str, *, cursor: str | None, limit: int) -> Page[CuratedWikiPage]:
        return self.repository.list_pages(project_id, cursor=cursor, limit=limit)

    def conflict_detail(self, project_id: str, conflict_id: str) -> dict[str, object]:
        conflict = self._require_conflict(project_id, conflict_id)
        left = self.repository.get_claim(project_id, conflict.left_claim_id, conflict.left_claim_revision)
        right = self.repository.get_claim(project_id, conflict.right_claim_id, conflict.right_claim_revision)
        return {
            "conflict": conflict.to_dict(),
            "left_claim": left.to_dict() if left else None,
            "right_claim": right.to_dict() if right else None,
            "timeline": [asdict(item) for item in self.repository.list_audit(project_id, conflict_id)],
        }

    def metrics_report(self, project_id: str) -> dict[str, object]:
        health = self.repository.latest_health(project_id)
        return {
            "schema": "knowledge_hygiene_metrics.v1",
            "project_id": project_id,
            "deterministic": {
                "measured": True,
                "counts": dict(health.counts) if health else {},
                "coverage": health.coverage.value if health else "unknown",
            },
            "llm": {
                "measured": False,
                "provider": None,
                "tokens": None,
                "latency_ms": None,
                "reason_code": "no_llm_measurement_available",
            },
        }

    def _require_enabled(self) -> None:
        if not self.config.enabled or self.config.mode == "disabled":
            raise KnowledgeHygieneServiceError("knowledge_hygiene_disabled")

    def _require_manual(self) -> None:
        self._require_enabled()
        if self.config.mode != "manual":
            raise KnowledgeHygieneServiceError("knowledge_hygiene_manual_mode_required")

    def _require_run(self, project_id: str, run_id: str) -> KnowledgeHygieneRun:
        run = self.repository.get_run(project_id, run_id)
        if run is None:
            raise KnowledgeHygieneServiceError("run_not_found")
        return run

    def _require_conflict(self, project_id: str, conflict_id: str) -> KnowledgeConflict:
        conflict = self.repository.get_conflict(project_id, conflict_id)
        if conflict is None:
            raise KnowledgeHygieneServiceError("conflict_not_found")
        return conflict

    def _all_claims(self, project_id: str) -> tuple[KnowledgeClaim, ...]:
        return tuple(_collect_pages(lambda cursor: self.repository.list_claims(project_id, cursor=cursor, limit=500)))

    def _all_conflicts(self, project_id: str) -> tuple[KnowledgeConflict, ...]:
        return tuple(_collect_pages(lambda cursor: self.repository.list_conflicts(project_id, cursor=cursor, limit=500)))

    def _all_pages(self, project_id: str) -> tuple[CuratedWikiPage, ...]:
        return tuple(_collect_pages(lambda cursor: self.repository.list_pages(project_id, cursor=cursor, limit=500)))

    def _audit(
        self,
        *,
        project_id: str,
        aggregate_type: str,
        aggregate_id: str,
        event_type: str,
        actor_id: str,
        payload: Mapping[str, object],
    ) -> AuditRecord:
        return self.repository.append_audit(
            new_audit_record(
                project_id=project_id,
                aggregate_type=aggregate_type,
                aggregate_id=aggregate_id,
                event_type=event_type,
                actor_id=actor_id,
                payload=_redact(payload),
                created_at=self.clock(),
            )
        )


def _collect_pages(loader):
    cursor = None
    while True:
        page = loader(cursor)
        yield from page.items
        if page.next_cursor is None:
            return
        cursor = page.next_cursor


def _redact(value: object, *, key: str | None = None) -> object:
    if key and key.casefold() in _AUDIT_SECRET_KEYS:
        return "[redacted]"
    if isinstance(value, Mapping):
        return {str(item_key): _redact(item_value, key=str(item_key)) for item_key, item_value in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact(item) for item in value]
    return value


def _delta(current: int | None, previous: int | None) -> float | None:
    if current is None or previous is None:
        return None
    return float(current - previous)
