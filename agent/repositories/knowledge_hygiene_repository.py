"""Persistence ports and Hub-owned adapters for Knowledge Hygiene."""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass
from typing import Generic, Mapping, Protocol, TypeVar

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from agent.database import engine as default_engine
from agent.db_models import (
    CuratedWikiPageDB,
    KnowledgeClaimDB,
    KnowledgeConflictDB,
    KnowledgeConflictDecisionDB,
    KnowledgeCorrectionDB,
    KnowledgeHealthSnapshotDB,
    KnowledgeHygieneAuditEventDB,
    KnowledgeHygieneRunDB,
)
from ananta_contracts.knowledge_hygiene import (
    CorrectionProposal,
    CoverageState,
    CuratedWikiPage,
    KnowledgeClaim,
    KnowledgeConflict,
    KnowledgeConflictDecision,
    KnowledgeHealthSnapshot,
    KnowledgeHygieneRun,
)


T = TypeVar("T")


class KnowledgeHygieneRepositoryError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class Page(Generic[T]):
    items: tuple[T, ...]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class AuditRecord:
    event_id: str
    project_id: str
    aggregate_type: str
    aggregate_id: str
    event_type: str
    actor_id: str
    payload: Mapping[str, object]
    created_at: float


class KnowledgeHygieneRepository(Protocol):
    def put_claim(self, claim: KnowledgeClaim) -> KnowledgeClaim: ...
    def get_claim(self, project_id: str, claim_id: str, revision: int | None = None) -> KnowledgeClaim | None: ...
    def list_claims(self, project_id: str, *, cursor: str | None = None, limit: int = 100) -> Page[KnowledgeClaim]: ...
    def put_conflict(self, conflict: KnowledgeConflict) -> KnowledgeConflict: ...
    def get_conflict(self, project_id: str, conflict_id: str) -> KnowledgeConflict | None: ...
    def list_conflicts(self, project_id: str, *, state: str | None = None, cursor: str | None = None, limit: int = 100) -> Page[KnowledgeConflict]: ...
    def decide_conflict(self, conflict: KnowledgeConflict, decision: KnowledgeConflictDecision) -> KnowledgeConflict: ...
    def update_conflict(self, conflict: KnowledgeConflict, *, expected_version: int) -> KnowledgeConflict: ...
    def put_page(self, page: CuratedWikiPage) -> CuratedWikiPage: ...
    def get_page(self, project_id: str, slug: str, revision: int | None = None) -> CuratedWikiPage | None: ...
    def list_pages(self, project_id: str, *, cursor: str | None = None, limit: int = 100) -> Page[CuratedWikiPage]: ...
    def put_run(self, run: KnowledgeHygieneRun, *, expected_state: str | None = None) -> KnowledgeHygieneRun: ...
    def get_run(self, project_id: str, run_id: str) -> KnowledgeHygieneRun | None: ...
    def put_health(self, snapshot: KnowledgeHealthSnapshot) -> KnowledgeHealthSnapshot: ...
    def latest_health(self, project_id: str) -> KnowledgeHealthSnapshot | None: ...
    def put_correction(self, proposal: CorrectionProposal, *, state: str = "proposed") -> CorrectionProposal: ...
    def get_correction(self, project_id: str, correction_id: str) -> tuple[CorrectionProposal, str] | None: ...
    def update_correction(self, proposal: CorrectionProposal, *, expected_state: str, state: str) -> CorrectionProposal: ...
    def append_audit(self, event: AuditRecord) -> AuditRecord: ...
    def list_audit(self, project_id: str, aggregate_id: str, *, limit: int = 200) -> tuple[AuditRecord, ...]: ...


def conflict_pair_key(conflict: KnowledgeConflict) -> str:
    sides = sorted(
        (
            (conflict.left_claim_id, conflict.left_claim_revision),
            (conflict.right_claim_id, conflict.right_claim_revision),
        )
    )
    return f"{conflict.project_id}:{sides[0][0]}:{sides[0][1]}:{sides[1][0]}:{sides[1][1]}:{conflict.conflict_type}"


def _health_from_mapping(raw: Mapping[str, object]) -> KnowledgeHealthSnapshot:
    return KnowledgeHealthSnapshot(
        snapshot_id=str(raw["snapshot_id"]),
        project_id=str(raw["project_id"]),
        as_of=float(raw["as_of"]),
        scope_version=str(raw["scope_version"]),
        coverage=CoverageState(str(raw["coverage"])),
        counts=dict(raw.get("counts") or {}),
        oldest_open_age_seconds=float(raw["oldest_open_age_seconds"]) if raw.get("oldest_open_age_seconds") is not None else None,
        trend=dict(raw.get("trend") or {}),
        basis_digest=str(raw["basis_digest"]),
    )


def _correction_from_mapping(raw: Mapping[str, object]) -> CorrectionProposal:
    return CorrectionProposal(
        correction_id=str(raw["correction_id"]),
        project_id=str(raw["project_id"]),
        conflict_id=str(raw["conflict_id"]),
        source_id=str(raw["source_id"]),
        source_revision=str(raw["source_revision"]),
        source_locator=str(raw["source_locator"]),
        base_content_sha256=str(raw["base_content_sha256"]),
        proposed_content=str(raw["proposed_content"]),
        proposal_digest=str(raw["proposal_digest"]),
        proposed_by_run_id=str(raw["proposed_by_run_id"]),
        created_at=float(raw["created_at"]),
        writeback_approved_by=str(raw["writeback_approved_by"]) if raw.get("writeback_approved_by") else None,
        writeback_approved_at=float(raw["writeback_approved_at"]) if raw.get("writeback_approved_at") is not None else None,
    )


class InMemoryKnowledgeHygieneRepository:
    """Deterministic test adapter with the same CAS and scoping semantics as SQL."""

    def __init__(self) -> None:
        self._claims: dict[tuple[str, str, int], KnowledgeClaim] = {}
        self._claim_keys: dict[tuple[str, str], KnowledgeClaim] = {}
        self._conflicts: dict[tuple[str, str], KnowledgeConflict] = {}
        self._conflict_pairs: dict[str, KnowledgeConflict] = {}
        self._decisions: dict[tuple[str, str], KnowledgeConflictDecision] = {}
        self._pages: dict[tuple[str, str, int], CuratedWikiPage] = {}
        self._runs: dict[tuple[str, str], KnowledgeHygieneRun] = {}
        self._health: dict[str, list[KnowledgeHealthSnapshot]] = {}
        self._corrections: dict[tuple[str, str], tuple[CorrectionProposal, str]] = {}
        self._audit: dict[str, AuditRecord] = {}
        self._lock = threading.RLock()

    def put_claim(self, claim: KnowledgeClaim) -> KnowledgeClaim:
        with self._lock:
            idempotent = self._claim_keys.get((claim.project_id, claim.idempotency_key))
            if idempotent is not None:
                return idempotent
            key = (claim.project_id, claim.claim_id, claim.revision)
            existing = self._claims.get(key)
            if existing is not None and existing.record_digest != claim.record_digest:
                raise KnowledgeHygieneRepositoryError("claim_revision_conflict")
            self._claims[key] = claim
            self._claim_keys[(claim.project_id, claim.idempotency_key)] = claim
            return claim

    def get_claim(self, project_id: str, claim_id: str, revision: int | None = None) -> KnowledgeClaim | None:
        with self._lock:
            candidates = [
                item for (pid, cid, _), item in self._claims.items()
                if pid == project_id and cid == claim_id and (revision is None or item.revision == revision)
            ]
        return max(candidates, key=lambda item: item.revision) if candidates else None

    def list_claims(self, project_id: str, *, cursor: str | None = None, limit: int = 100) -> Page[KnowledgeClaim]:
        with self._lock:
            items = sorted(
                (item for (pid, _, _), item in self._claims.items() if pid == project_id),
                key=lambda item: (item.claim_id, item.revision),
            )
        return _page(items, cursor=cursor, limit=limit, key=lambda item: f"{item.claim_id}:{item.revision:08d}")

    def put_conflict(self, conflict: KnowledgeConflict) -> KnowledgeConflict:
        pair_key = conflict_pair_key(conflict)
        with self._lock:
            paired = self._conflict_pairs.get(pair_key)
            if paired is not None:
                return paired
            key = (conflict.project_id, conflict.conflict_id)
            existing = self._conflicts.get(key)
            if existing is not None and existing.basis_digest != conflict.basis_digest:
                raise KnowledgeHygieneRepositoryError("conflict_id_collision")
            self._conflicts[key] = conflict
            self._conflict_pairs[pair_key] = conflict
            return conflict

    def get_conflict(self, project_id: str, conflict_id: str) -> KnowledgeConflict | None:
        with self._lock:
            return self._conflicts.get((project_id, conflict_id))

    def list_conflicts(self, project_id: str, *, state: str | None = None, cursor: str | None = None, limit: int = 100) -> Page[KnowledgeConflict]:
        with self._lock:
            items = sorted(
                (
                    item for (pid, _), item in self._conflicts.items()
                    if pid == project_id and (state is None or item.state.value == state)
                ),
                key=lambda item: item.conflict_id,
            )
        return _page(items, cursor=cursor, limit=limit, key=lambda item: item.conflict_id)

    def decide_conflict(self, conflict: KnowledgeConflict, decision: KnowledgeConflictDecision) -> KnowledgeConflict:
        with self._lock:
            current = self._conflicts.get((conflict.project_id, conflict.conflict_id))
            if current is None:
                raise KnowledgeHygieneRepositoryError("conflict_not_found")
            decision_key = (decision.project_id, decision.decision_id)
            existing_decision = self._decisions.get(decision_key)
            if existing_decision is not None:
                if existing_decision.idempotency_payload() != decision.idempotency_payload():
                    raise KnowledgeHygieneRepositoryError("decision_id_collision")
                return current
            if current.version != decision.expected_conflict_version or current.basis_digest != decision.basis_digest:
                raise KnowledgeHygieneRepositoryError("stale_conflict_revision")
            if current.state.value not in {"open", "reopened"}:
                raise KnowledgeHygieneRepositoryError("conflict_not_decidable")
            if conflict.version != current.version + 1:
                raise KnowledgeHygieneRepositoryError("invalid_conflict_cas_version")
            self._conflicts[(conflict.project_id, conflict.conflict_id)] = conflict
            self._replace_conflict_pair(conflict)
            self._decisions[decision_key] = decision
            return conflict

    def update_conflict(self, conflict: KnowledgeConflict, *, expected_version: int) -> KnowledgeConflict:
        with self._lock:
            key = (conflict.project_id, conflict.conflict_id)
            current = self._conflicts.get(key)
            if current is None:
                raise KnowledgeHygieneRepositoryError("conflict_not_found")
            if current.version != expected_version or conflict.version != expected_version + 1:
                raise KnowledgeHygieneRepositoryError("stale_conflict_revision")
            self._conflicts[key] = conflict
            self._replace_conflict_pair(conflict)
            return conflict

    def _replace_conflict_pair(self, conflict: KnowledgeConflict) -> None:
        stale_keys = [
            key for key, item in self._conflict_pairs.items()
            if item.project_id == conflict.project_id and item.conflict_id == conflict.conflict_id
        ]
        for key in stale_keys:
            self._conflict_pairs.pop(key, None)
        self._conflict_pairs[conflict_pair_key(conflict)] = conflict

    def put_page(self, page: CuratedWikiPage) -> CuratedWikiPage:
        key = (page.project_id, page.slug, page.revision)
        with self._lock:
            existing = self._pages.get(key)
            if existing is not None and existing.content_hash != page.content_hash:
                raise KnowledgeHygieneRepositoryError("wiki_revision_conflict")
            self._pages[key] = page
            return existing or page

    def get_page(self, project_id: str, slug: str, revision: int | None = None) -> CuratedWikiPage | None:
        with self._lock:
            candidates = [
                item for (pid, page_slug, _), item in self._pages.items()
                if pid == project_id and page_slug == slug and (revision is None or item.revision == revision)
            ]
        return max(candidates, key=lambda item: item.revision) if candidates else None

    def list_pages(self, project_id: str, *, cursor: str | None = None, limit: int = 100) -> Page[CuratedWikiPage]:
        with self._lock:
            latest: dict[str, CuratedWikiPage] = {}
            for (pid, slug, _), item in self._pages.items():
                if pid == project_id and (slug not in latest or latest[slug].revision < item.revision):
                    latest[slug] = item
        return _page(sorted(latest.values(), key=lambda item: item.slug), cursor=cursor, limit=limit, key=lambda item: item.slug)

    def put_run(self, run: KnowledgeHygieneRun, *, expected_state: str | None = None) -> KnowledgeHygieneRun:
        key = (run.project_id, run.run_id)
        with self._lock:
            current = self._runs.get(key)
            if expected_state is not None and (current is None or current.state.value != expected_state):
                raise KnowledgeHygieneRepositoryError("stale_run_state")
            if current is not None and expected_state is None and current.assignment_digest != run.assignment_digest:
                raise KnowledgeHygieneRepositoryError("run_id_collision")
            if current is not None and expected_state is None:
                return current
            self._runs[key] = run
            return run

    def get_run(self, project_id: str, run_id: str) -> KnowledgeHygieneRun | None:
        with self._lock:
            return self._runs.get((project_id, run_id))

    def put_health(self, snapshot: KnowledgeHealthSnapshot) -> KnowledgeHealthSnapshot:
        with self._lock:
            self._health.setdefault(snapshot.project_id, []).append(snapshot)
        return snapshot

    def latest_health(self, project_id: str) -> KnowledgeHealthSnapshot | None:
        with self._lock:
            items = self._health.get(project_id, [])
            return max(items, key=lambda item: item.as_of) if items else None

    def put_correction(self, proposal: CorrectionProposal, *, state: str = "proposed") -> CorrectionProposal:
        key = (proposal.project_id, proposal.correction_id)
        with self._lock:
            existing = self._corrections.get(key)
            if existing is not None:
                if existing[0].proposal_digest != proposal.proposal_digest:
                    raise KnowledgeHygieneRepositoryError("correction_id_collision")
                return existing[0]
            self._corrections[key] = (proposal, state)
        return proposal

    def get_correction(self, project_id: str, correction_id: str) -> tuple[CorrectionProposal, str] | None:
        with self._lock:
            return self._corrections.get((project_id, correction_id))

    def update_correction(self, proposal: CorrectionProposal, *, expected_state: str, state: str) -> CorrectionProposal:
        key = (proposal.project_id, proposal.correction_id)
        with self._lock:
            current = self._corrections.get(key)
            if current is None or current[1] != expected_state:
                raise KnowledgeHygieneRepositoryError("stale_correction_state")
            self._corrections[key] = (proposal, state)
        return proposal

    def append_audit(self, event: AuditRecord) -> AuditRecord:
        with self._lock:
            existing = self._audit.get(event.event_id)
            if existing is not None and existing != event:
                raise KnowledgeHygieneRepositoryError("audit_event_id_collision")
            self._audit[event.event_id] = event
            return existing or event

    def list_audit(self, project_id: str, aggregate_id: str, *, limit: int = 200) -> tuple[AuditRecord, ...]:
        with self._lock:
            items = [item for item in self._audit.values() if item.project_id == project_id and item.aggregate_id == aggregate_id]
        items.sort(key=lambda item: (item.created_at, item.event_id))
        return tuple(items[-limit:])


def _page(items: list[T], *, cursor: str | None, limit: int, key) -> Page[T]:
    bounded = max(1, min(int(limit), 500))
    filtered = [item for item in items if cursor is None or key(item) > cursor]
    selected = filtered[: bounded + 1]
    next_cursor = key(selected[bounded - 1]) if len(selected) > bounded else None
    return Page(tuple(selected[:bounded]), next_cursor)


class SqlKnowledgeHygieneRepository:
    """Cross-container Hub source of truth backed by the configured SQL engine."""

    def __init__(self, *, db_engine=default_engine) -> None:
        self._engine = db_engine

    def put_claim(self, claim: KnowledgeClaim) -> KnowledgeClaim:
        with Session(self._engine) as db:
            existing = db.exec(
                select(KnowledgeClaimDB).where(
                    KnowledgeClaimDB.project_id == claim.project_id,
                    KnowledgeClaimDB.idempotency_key == claim.idempotency_key,
                )
            ).first()
            if existing is not None:
                return KnowledgeClaim.from_mapping(existing.payload)
            collision = db.exec(
                select(KnowledgeClaimDB).where(
                    KnowledgeClaimDB.project_id == claim.project_id,
                    KnowledgeClaimDB.claim_id == claim.claim_id,
                    KnowledgeClaimDB.revision == claim.revision,
                )
            ).first()
            if collision is not None:
                if collision.record_digest != claim.record_digest:
                    raise KnowledgeHygieneRepositoryError("claim_revision_conflict")
                return KnowledgeClaim.from_mapping(collision.payload)
            row = KnowledgeClaimDB(
                claim_id=claim.claim_id,
                project_id=claim.project_id,
                revision=claim.revision,
                idempotency_key=claim.idempotency_key,
                source_id=claim.source_id,
                source_revision=claim.source_revision,
                source_locator=claim.source_locator,
                record_digest=claim.record_digest,
                payload=claim.to_dict(),
                created_at=claim.created_at,
            )
            db.add(row)
            try:
                db.commit()
            except IntegrityError as exc:
                db.rollback()
                replay = db.exec(
                    select(KnowledgeClaimDB).where(
                        KnowledgeClaimDB.project_id == claim.project_id,
                        KnowledgeClaimDB.idempotency_key == claim.idempotency_key,
                    )
                ).first()
                if replay is None:
                    raise KnowledgeHygieneRepositoryError("claim_write_conflict") from exc
                return KnowledgeClaim.from_mapping(replay.payload)
        return claim

    def get_claim(self, project_id: str, claim_id: str, revision: int | None = None) -> KnowledgeClaim | None:
        statement = select(KnowledgeClaimDB).where(
            KnowledgeClaimDB.project_id == project_id,
            KnowledgeClaimDB.claim_id == claim_id,
        )
        if revision is not None:
            statement = statement.where(KnowledgeClaimDB.revision == revision)
        statement = statement.order_by(KnowledgeClaimDB.revision.desc()).limit(1)
        with Session(self._engine) as db:
            row = db.exec(statement).first()
            return KnowledgeClaim.from_mapping(row.payload) if row else None

    def list_claims(self, project_id: str, *, cursor: str | None = None, limit: int = 100) -> Page[KnowledgeClaim]:
        bounded = max(1, min(int(limit), 500))
        statement = select(KnowledgeClaimDB).where(KnowledgeClaimDB.project_id == project_id)
        if cursor:
            claim_id, _, revision = cursor.partition(":")
            statement = statement.where(
                (KnowledgeClaimDB.claim_id > claim_id)
                | ((KnowledgeClaimDB.claim_id == claim_id) & (KnowledgeClaimDB.revision > int(revision or 0)))
            )
        statement = statement.order_by(KnowledgeClaimDB.claim_id, KnowledgeClaimDB.revision).limit(bounded + 1)
        with Session(self._engine) as db:
            rows = list(db.exec(statement))
        next_cursor = f"{rows[bounded - 1].claim_id}:{rows[bounded - 1].revision:08d}" if len(rows) > bounded else None
        return Page(tuple(KnowledgeClaim.from_mapping(row.payload) for row in rows[:bounded]), next_cursor)

    def put_conflict(self, conflict: KnowledgeConflict) -> KnowledgeConflict:
        pair_key = conflict_pair_key(conflict)
        with Session(self._engine) as db:
            existing = db.exec(select(KnowledgeConflictDB).where(KnowledgeConflictDB.pair_key == pair_key)).first()
            if existing is not None:
                return KnowledgeConflict.from_mapping(existing.payload)
            row = KnowledgeConflictDB(
                id=conflict.conflict_id,
                project_id=conflict.project_id,
                pair_key=pair_key,
                state=conflict.state.value,
                severity=conflict.severity,
                version=conflict.version,
                basis_digest=conflict.basis_digest,
                payload=conflict.to_dict(),
                created_at=conflict.created_at,
                updated_at=conflict.updated_at,
            )
            db.add(row)
            try:
                db.commit()
            except IntegrityError as exc:
                db.rollback()
                replay = db.exec(select(KnowledgeConflictDB).where(KnowledgeConflictDB.pair_key == pair_key)).first()
                if replay is None:
                    raise KnowledgeHygieneRepositoryError("conflict_write_conflict") from exc
                return KnowledgeConflict.from_mapping(replay.payload)
        return conflict

    def get_conflict(self, project_id: str, conflict_id: str) -> KnowledgeConflict | None:
        with Session(self._engine) as db:
            row = db.get(KnowledgeConflictDB, conflict_id)
            if row is None or row.project_id != project_id:
                return None
            return KnowledgeConflict.from_mapping(row.payload)

    def list_conflicts(self, project_id: str, *, state: str | None = None, cursor: str | None = None, limit: int = 100) -> Page[KnowledgeConflict]:
        bounded = max(1, min(int(limit), 500))
        statement = select(KnowledgeConflictDB).where(KnowledgeConflictDB.project_id == project_id)
        if state:
            statement = statement.where(KnowledgeConflictDB.state == state)
        if cursor:
            statement = statement.where(KnowledgeConflictDB.id > cursor)
        statement = statement.order_by(KnowledgeConflictDB.id).limit(bounded + 1)
        with Session(self._engine) as db:
            rows = list(db.exec(statement))
        next_cursor = rows[bounded - 1].id if len(rows) > bounded else None
        return Page(tuple(KnowledgeConflict.from_mapping(row.payload) for row in rows[:bounded]), next_cursor)

    def decide_conflict(self, conflict: KnowledgeConflict, decision: KnowledgeConflictDecision) -> KnowledgeConflict:
        with Session(self._engine) as db:
            decision_record_id = f"{decision.project_id}:{decision.decision_id}"
            replay = db.get(KnowledgeConflictDecisionDB, decision_record_id)
            row = db.exec(
                select(KnowledgeConflictDB)
                .where(
                    KnowledgeConflictDB.id == conflict.conflict_id,
                    KnowledgeConflictDB.project_id == conflict.project_id,
                )
                .with_for_update()
            ).first()
            if row is None:
                raise KnowledgeHygieneRepositoryError("conflict_not_found")
            if replay is not None:
                replay_payload = dict(replay.payload)
                replay_payload.pop("created_at", None)
                if replay_payload != decision.idempotency_payload():
                    raise KnowledgeHygieneRepositoryError("decision_id_collision")
                return KnowledgeConflict.from_mapping(row.payload)
            if row.version != decision.expected_conflict_version or row.basis_digest != decision.basis_digest:
                raise KnowledgeHygieneRepositoryError("stale_conflict_revision")
            if row.state not in {"open", "reopened"}:
                raise KnowledgeHygieneRepositoryError("conflict_not_decidable")
            if conflict.version != row.version + 1:
                raise KnowledgeHygieneRepositoryError("invalid_conflict_cas_version")
            row.state = conflict.state.value
            row.version = conflict.version
            row.pair_key = conflict_pair_key(conflict)
            row.basis_digest = conflict.basis_digest
            row.payload = conflict.to_dict()
            row.updated_at = conflict.updated_at
            db.add(row)
            db.add(
                KnowledgeConflictDecisionDB(
                    id=decision_record_id,
                    decision_id=decision.decision_id,
                    project_id=decision.project_id,
                    conflict_id=decision.conflict_id,
                    actor_id=decision.actor_id,
                    basis_digest=decision.basis_digest,
                    payload=decision.to_dict(),
                    created_at=decision.created_at,
                )
            )
            db.commit()
            return conflict

    def update_conflict(self, conflict: KnowledgeConflict, *, expected_version: int) -> KnowledgeConflict:
        with Session(self._engine) as db:
            row = db.exec(
                select(KnowledgeConflictDB)
                .where(
                    KnowledgeConflictDB.id == conflict.conflict_id,
                    KnowledgeConflictDB.project_id == conflict.project_id,
                )
                .with_for_update()
            ).first()
            if row is None:
                raise KnowledgeHygieneRepositoryError("conflict_not_found")
            if row.version != expected_version or conflict.version != expected_version + 1:
                raise KnowledgeHygieneRepositoryError("stale_conflict_revision")
            row.state = conflict.state.value
            row.version = conflict.version
            row.pair_key = conflict_pair_key(conflict)
            row.basis_digest = conflict.basis_digest
            row.payload = conflict.to_dict()
            row.updated_at = conflict.updated_at
            db.add(row)
            db.commit()
            return conflict

    def put_page(self, page: CuratedWikiPage) -> CuratedWikiPage:
        record_id = f"{page.page_id}:{page.revision}"
        with Session(self._engine) as db:
            existing = db.get(CuratedWikiPageDB, record_id)
            if existing is not None:
                if existing.content_hash != page.content_hash:
                    raise KnowledgeHygieneRepositoryError("wiki_revision_conflict")
                return CuratedWikiPage.from_mapping(existing.payload)
            db.add(
                CuratedWikiPageDB(
                    id=record_id,
                    page_id=page.page_id,
                    project_id=page.project_id,
                    slug=page.slug,
                    revision=page.revision,
                    content_hash=page.content_hash,
                    coverage=page.coverage.value,
                    payload=page.to_dict(),
                    created_at=page.created_at,
                )
            )
            db.commit()
        return page

    def get_page(self, project_id: str, slug: str, revision: int | None = None) -> CuratedWikiPage | None:
        statement = select(CuratedWikiPageDB).where(
            CuratedWikiPageDB.project_id == project_id,
            CuratedWikiPageDB.slug == slug,
        )
        if revision is not None:
            statement = statement.where(CuratedWikiPageDB.revision == revision)
        statement = statement.order_by(CuratedWikiPageDB.revision.desc()).limit(1)
        with Session(self._engine) as db:
            row = db.exec(statement).first()
            return CuratedWikiPage.from_mapping(row.payload) if row else None

    def list_pages(self, project_id: str, *, cursor: str | None = None, limit: int = 100) -> Page[CuratedWikiPage]:
        bounded = max(1, min(int(limit), 500))
        statement = select(CuratedWikiPageDB).where(CuratedWikiPageDB.project_id == project_id)
        if cursor:
            statement = statement.where(CuratedWikiPageDB.slug > cursor)
        statement = statement.order_by(CuratedWikiPageDB.slug, CuratedWikiPageDB.revision.desc())
        with Session(self._engine) as db:
            rows = list(db.exec(statement))
        latest: dict[str, CuratedWikiPageDB] = {}
        for row in rows:
            latest.setdefault(row.slug, row)
            if len(latest) > bounded:
                break
        selected = list(latest.values())
        next_cursor = selected[bounded - 1].slug if len(selected) > bounded else None
        return Page(tuple(CuratedWikiPage.from_mapping(row.payload) for row in selected[:bounded]), next_cursor)

    def put_run(self, run: KnowledgeHygieneRun, *, expected_state: str | None = None) -> KnowledgeHygieneRun:
        with Session(self._engine) as db:
            row = db.exec(
                select(KnowledgeHygieneRunDB)
                .where(KnowledgeHygieneRunDB.run_id == run.run_id, KnowledgeHygieneRunDB.project_id == run.project_id)
                .with_for_update()
            ).first()
            if expected_state is not None and (row is None or row.state != expected_state):
                raise KnowledgeHygieneRepositoryError("stale_run_state")
            if row is None:
                row = KnowledgeHygieneRunDB(
                    id=f"{run.project_id}:{run.run_id}",
                    run_id=run.run_id,
                    project_id=run.project_id,
                    state=run.state.value,
                    assignment_digest=run.assignment_digest,
                    result_digest=run.result_digest,
                    checkpoint=run.checkpoint,
                    payload=run.to_dict(),
                    created_at=run.created_at,
                    updated_at=run.updated_at,
                )
            elif expected_state is None:
                if row.assignment_digest != run.assignment_digest:
                    raise KnowledgeHygieneRepositoryError("run_id_collision")
                return KnowledgeHygieneRun.from_mapping(row.payload)
            else:
                row.state = run.state.value
                row.result_digest = run.result_digest
                row.checkpoint = run.checkpoint
                row.payload = run.to_dict()
                row.updated_at = run.updated_at
            db.add(row)
            db.commit()
        return run

    def get_run(self, project_id: str, run_id: str) -> KnowledgeHygieneRun | None:
        with Session(self._engine) as db:
            row = db.exec(
                select(KnowledgeHygieneRunDB).where(
                    KnowledgeHygieneRunDB.project_id == project_id,
                    KnowledgeHygieneRunDB.run_id == run_id,
                )
            ).first()
            if row is None:
                return None
            return KnowledgeHygieneRun.from_mapping(row.payload)

    def put_health(self, snapshot: KnowledgeHealthSnapshot) -> KnowledgeHealthSnapshot:
        with Session(self._engine) as db:
            existing = db.get(KnowledgeHealthSnapshotDB, snapshot.snapshot_id)
            if existing is not None:
                return _health_from_mapping(existing.payload)
            db.add(
                KnowledgeHealthSnapshotDB(
                    id=snapshot.snapshot_id,
                    project_id=snapshot.project_id,
                    scope_version=snapshot.scope_version,
                    coverage=snapshot.coverage.value,
                    basis_digest=snapshot.basis_digest,
                    payload=snapshot.to_dict(),
                    as_of=snapshot.as_of,
                )
            )
            db.commit()
        return snapshot

    def latest_health(self, project_id: str) -> KnowledgeHealthSnapshot | None:
        with Session(self._engine) as db:
            row = db.exec(
                select(KnowledgeHealthSnapshotDB)
                .where(KnowledgeHealthSnapshotDB.project_id == project_id)
                .order_by(KnowledgeHealthSnapshotDB.as_of.desc())
                .limit(1)
            ).first()
            return _health_from_mapping(row.payload) if row else None

    def put_correction(self, proposal: CorrectionProposal, *, state: str = "proposed") -> CorrectionProposal:
        with Session(self._engine) as db:
            record_id = f"{proposal.project_id}:{proposal.correction_id}"
            existing = db.get(KnowledgeCorrectionDB, record_id)
            if existing is not None:
                if existing.proposal_digest != proposal.proposal_digest:
                    raise KnowledgeHygieneRepositoryError("correction_id_collision")
                return _correction_from_mapping(existing.payload)
            db.add(
                KnowledgeCorrectionDB(
                    id=record_id,
                    correction_id=proposal.correction_id,
                    project_id=proposal.project_id,
                    conflict_id=proposal.conflict_id,
                    source_id=proposal.source_id,
                    proposal_digest=proposal.proposal_digest,
                    state=state,
                    payload=proposal.to_dict(),
                    created_at=proposal.created_at,
                    updated_at=proposal.created_at,
                )
            )
            db.commit()
        return proposal

    def get_correction(self, project_id: str, correction_id: str) -> tuple[CorrectionProposal, str] | None:
        with Session(self._engine) as db:
            row = db.exec(
                select(KnowledgeCorrectionDB).where(
                    KnowledgeCorrectionDB.project_id == project_id,
                    KnowledgeCorrectionDB.correction_id == correction_id,
                )
            ).first()
            if row is None:
                return None
            return _correction_from_mapping(row.payload), row.state

    def update_correction(self, proposal: CorrectionProposal, *, expected_state: str, state: str) -> CorrectionProposal:
        with Session(self._engine) as db:
            row = db.exec(
                select(KnowledgeCorrectionDB)
                .where(
                    KnowledgeCorrectionDB.correction_id == proposal.correction_id,
                    KnowledgeCorrectionDB.project_id == proposal.project_id,
                )
                .with_for_update()
            ).first()
            if row is None or row.state != expected_state:
                raise KnowledgeHygieneRepositoryError("stale_correction_state")
            row.state = state
            row.payload = proposal.to_dict()
            row.updated_at = proposal.writeback_approved_at or proposal.created_at
            db.add(row)
            db.commit()
        return proposal

    def append_audit(self, event: AuditRecord) -> AuditRecord:
        with Session(self._engine) as db:
            existing = db.get(KnowledgeHygieneAuditEventDB, event.event_id)
            if existing is not None:
                return event
            db.add(
                KnowledgeHygieneAuditEventDB(
                    id=event.event_id,
                    project_id=event.project_id,
                    aggregate_type=event.aggregate_type,
                    aggregate_id=event.aggregate_id,
                    event_type=event.event_type,
                    actor_id=event.actor_id,
                    payload=dict(event.payload),
                    created_at=event.created_at,
                )
            )
            db.commit()
        return event

    def list_audit(self, project_id: str, aggregate_id: str, *, limit: int = 200) -> tuple[AuditRecord, ...]:
        with Session(self._engine) as db:
            rows = list(
                db.exec(
                    select(KnowledgeHygieneAuditEventDB)
                    .where(
                        KnowledgeHygieneAuditEventDB.project_id == project_id,
                        KnowledgeHygieneAuditEventDB.aggregate_id == aggregate_id,
                    )
                    .order_by(KnowledgeHygieneAuditEventDB.created_at, KnowledgeHygieneAuditEventDB.id)
                    .limit(max(1, min(limit, 500)))
                )
            )
        return tuple(
            AuditRecord(
                event_id=row.id,
                project_id=row.project_id,
                aggregate_type=row.aggregate_type,
                aggregate_id=row.aggregate_id,
                event_type=row.event_type,
                actor_id=row.actor_id,
                payload=row.payload,
                created_at=row.created_at,
            )
            for row in rows
        )


def new_audit_record(
    *,
    project_id: str,
    aggregate_type: str,
    aggregate_id: str,
    event_type: str,
    actor_id: str,
    payload: Mapping[str, object],
    created_at: float,
) -> AuditRecord:
    return AuditRecord(
        event_id=str(uuid.uuid4()),
        project_id=project_id,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        event_type=event_type,
        actor_id=actor_id,
        payload=payload,
        created_at=created_at,
    )
