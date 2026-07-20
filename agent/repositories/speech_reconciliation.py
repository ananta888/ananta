"""Transactional Hub repository for restart-stable speech reconciliation."""

from __future__ import annotations

import threading
import time
from contextlib import nullcontext
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from agent.database import engine
from agent.db_models.speech_reconciliation import (
    SpeechReconciliationArtifactDB,
    SpeechReconciliationAttemptDB,
    SpeechReconciliationBudgetLedgerDB,
    SpeechReconciliationCheckpointDB,
    SpeechReconciliationJobDB,
    SpeechReconciliationMutationDB,
)
from agent.repositories.semantic_media_audit_outbox import SqlSemanticMediaAuditOutbox
from agent.repositories.speech_evidence_lineage import (
    SpeechEvidenceLineageRepository,
    SpeechLineageEdge,
    SpeechLineageNode,
    get_speech_evidence_lineage_repository,
)
from agent.services.semantic_media_audit_service import (
    SemanticMediaAuditEvent,
    SemanticMediaAuditPort,
)
from agent.services.speech_reconciliation_state_machine import SpeechReconciliationStateMachine
from ananta_contracts.speech_reconciliation import (
    CONTRACT_VERSION,
    SpeechReconciliationBudgetLedger,
    SpeechReconciliationCheckpoint,
    SpeechReconciliationJob,
    SpeechReconciliationResult,
    SpeechResourceVector,
    assert_result_matches_job,
)
from ananta_contracts.speech_reconciliation_state import STAGES

_SQLITE_WRITE_GUARD = threading.RLock()


class SpeechReconciliationRepositoryError(RuntimeError):
    def __init__(self, reason_code: str, *, status_code: int = 409) -> None:
        self.reason_code = reason_code
        self.status_code = status_code
        super().__init__(reason_code)


@dataclass(frozen=True)
class SpeechReconciliationJobCreate:
    job_id: str
    tenant_id: str
    owner_subject: str
    pair_scope_digest: str
    idempotency_key_digest: str
    request_digest: str
    consent_id: str
    consent_version: int
    revocation_epoch: int
    input_manifest_digest: str
    input_lineage_digest: str
    input_artifact_ref: str
    policy_digest: str
    research_policy_ref: str | None
    budget_plan: Mapping[str, object]
    source_duration_ms: int
    max_compute_factor: int
    key_epoch: int
    deadline_at_ms: int
    current_compute_factor: int | None = None
    training_budget: Mapping[str, int] | None = None


@dataclass(frozen=True)
class SpeechReconciliationJobRecord:
    id: str
    tenant_id: str
    owner_subject: str
    pair_scope_digest: str
    request_digest: str
    state: str
    stage: str
    reason_code: str
    consent_id: str
    consent_version: int
    revocation_epoch: int
    input_manifest_digest: str
    input_lineage_digest: str
    input_artifact_ref: str
    policy_digest: str
    research_policy_ref: str | None
    budget_plan: dict[str, object]
    source_duration_ms: int
    max_compute_factor: int
    ledger_sequence: int
    key_epoch: int
    deadline_at_ms: int
    active_attempt_id: str | None
    fencing_epoch: int
    checkpoint_count: int
    resolved_count: int
    unresolved_count: int
    rejected_count: int
    quarantined_count: int
    version: int
    created_at_ms: int
    updated_at_ms: int
    finished_at_ms: int | None
    current_compute_factor: int = 1
    quality_history: list[dict[str, object]] = field(default_factory=list)
    training_budget: dict[str, int] | None = None


@dataclass(frozen=True)
class SpeechReconciliationMutationResult:
    """Durable outcome of one externally requested Hub mutation.

    ``applied`` is true only for the transaction that changed job state.  It
    lets the service invoke non-database task adapters once without turning an
    idempotent replay into a second cancellation side effect.
    """

    job: SpeechReconciliationJobRecord
    applied: bool
    affected_attempt_id: str | None = None
    affected_fencing_epoch: int | None = None


@dataclass(frozen=True)
class SpeechReconciliationAttemptRecord:
    id: str
    job_id: str
    attempt_number: int
    state: str
    fencing_token_digest: str
    fencing_epoch: int
    lease_expires_at_ms: int
    deadline_at_ms: int
    last_heartbeat_at_ms: int
    checkpoint_sequence: int
    checkpoint_digest: str | None
    version: int


@dataclass(frozen=True)
class SpeechReconciliationCollectibleAttempt:
    """Content-free current-attempt projection for the Hub result collector."""

    tenant_id: str
    owner_subject: str
    job_state: str
    job_contract: SpeechReconciliationJob
    attempt_version: int
    lease_expires_at_ms: int


class SpeechReconciliationRepository:
    def __init__(
        self,
        *,
        lineage: SpeechEvidenceLineageRepository | None = None,
        audit: SemanticMediaAuditPort | None = None,
    ) -> None:
        self._lineage = lineage or get_speech_evidence_lineage_repository()
        self._states = SpeechReconciliationStateMachine()
        self._audit = audit

    @property
    def transactional_audit(self) -> bool:
        return self._audit is not None

    def configure_audit(self, audit: SemanticMediaAuditPort | None) -> None:
        """Composition-root hook used before any Hub mutation is accepted."""

        self._audit = audit

    def _audit_event(
        self,
        *,
        tenant_id: str,
        job_id: str,
        idempotency_key: str,
        event_type: str,
        transition: str,
        reason_code: str,
        epoch: int,
        lease_ref: str | None = None,
    ) -> SemanticMediaAuditEvent | None:
        if self._audit is None:
            return None
        return self._audit.prepare_transition(
            idempotency_key=idempotency_key,
            tenant_id=tenant_id,
            scope=f"speech-job:{job_id}",
            event_type=event_type,
            transition=transition,
            reason_code=reason_code,
            epoch=max(1, epoch),
            lease_ref=lease_ref,
            job_ref=job_id,
        )

    @staticmethod
    def _enqueue_audit(session: Session, event: SemanticMediaAuditEvent | None) -> None:
        if event is not None:
            SqlSemanticMediaAuditOutbox.enqueue_in_session(session, event)

    def create_job(
        self, spec: SpeechReconciliationJobCreate, *, now_ms: int | None = None
    ) -> tuple[SpeechReconciliationJobRecord, bool]:
        # Tests and native single-process deployments use one StaticPool
        # SQLite connection. The guard prevents two threads from rolling back
        # each other's connection-level transaction; database uniqueness
        # constraints remain the cross-process/multi-Hub authority.
        guard = _SQLITE_WRITE_GUARD if engine.dialect.name == "sqlite" else nullcontext()
        with guard:
            return self._create_job(spec, now_ms=now_ms)

    def _create_job(
        self, spec: SpeechReconciliationJobCreate, *, now_ms: int | None = None
    ) -> tuple[SpeechReconciliationJobRecord, bool]:
        now = _now(now_ms)
        audit_event = self._audit_event(
            tenant_id=spec.tenant_id,
            job_id=spec.job_id,
            idempotency_key=f"speech-reconciliation:create:{spec.job_id}",
            event_type="semantic_job",
            transition="created",
            reason_code="speech_reconciliation_admitted",
            epoch=max(1, spec.revocation_epoch + 1),
        )
        row = SpeechReconciliationJobDB(
            id=spec.job_id,
            tenant_id=spec.tenant_id,
            owner_subject=spec.owner_subject,
            pair_scope_digest=spec.pair_scope_digest,
            idempotency_key_digest=spec.idempotency_key_digest,
            request_digest=spec.request_digest,
            consent_id=spec.consent_id,
            consent_version=spec.consent_version,
            revocation_epoch=spec.revocation_epoch,
            input_manifest_digest=spec.input_manifest_digest,
            input_lineage_digest=spec.input_lineage_digest,
            input_artifact_ref=spec.input_artifact_ref,
            policy_digest=spec.policy_digest,
            research_policy_ref=spec.research_policy_ref,
            budget_plan=_validate_budget_plan(spec.budget_plan, factor=spec.max_compute_factor),
            source_duration_ms=spec.source_duration_ms,
            max_compute_factor=spec.max_compute_factor,
            current_compute_factor=(
                spec.current_compute_factor
                if spec.current_compute_factor is not None
                else min(10, spec.max_compute_factor)
            ),
            training_budget=(dict(spec.training_budget) if spec.training_budget is not None else None),
            key_epoch=spec.key_epoch,
            deadline_at_ms=spec.deadline_at_ms,
            created_at_ms=now,
            updated_at_ms=now,
        )
        with Session(engine) as session:
            existing = session.exec(
                select(SpeechReconciliationJobDB).where(
                    SpeechReconciliationJobDB.tenant_id == spec.tenant_id,
                    SpeechReconciliationJobDB.owner_subject == spec.owner_subject,
                    SpeechReconciliationJobDB.idempotency_key_digest == spec.idempotency_key_digest,
                )
            ).first()
            if existing is not None:
                if existing.request_digest != spec.request_digest:
                    raise SpeechReconciliationRepositoryError("speech_reconciliation_idempotency_conflict")
                return _job(existing), False
            session.add(row)
            try:
                outbox = self._lineage.stage(
                    session,
                    tenant_id=spec.tenant_id,
                    owner_subject=spec.owner_subject,
                    nodes=(
                        SpeechLineageNode(
                            "manifest",
                            spec.input_manifest_digest,
                            consent_id=spec.consent_id,
                            revocation_epoch=spec.revocation_epoch,
                        ),
                        SpeechLineageNode(
                            "reconciliation",
                            spec.request_digest,
                            consent_id=spec.consent_id,
                            revocation_epoch=spec.revocation_epoch,
                        ),
                    ),
                    edges=(
                        SpeechLineageEdge(
                            "manifest",
                            spec.input_manifest_digest,
                            "reconciliation",
                            spec.request_digest,
                            "input_to",
                        ),
                    ),
                    now_ms=now,
                )
                self._enqueue_audit(session, audit_event)
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                winner = session.exec(
                    select(SpeechReconciliationJobDB).where(
                        SpeechReconciliationJobDB.tenant_id == spec.tenant_id,
                        SpeechReconciliationJobDB.owner_subject == spec.owner_subject,
                        SpeechReconciliationJobDB.idempotency_key_digest == spec.idempotency_key_digest,
                    )
                ).first()
                if winner is not None and winner.request_digest == spec.request_digest:
                    return _job(winner), False
                raise SpeechReconciliationRepositoryError("speech_reconciliation_write_conflict") from exc
            session.refresh(row)
        self._lineage.process_outbox(event_digest=outbox, tenant_id=spec.tenant_id, owner_subject=spec.owner_subject)
        return _job(row), True

    def get_job(self, *, tenant_id: str, owner_subject: str, job_id: str) -> SpeechReconciliationJobRecord | None:
        with Session(engine) as session:
            row = session.exec(
                select(SpeechReconciliationJobDB).where(
                    SpeechReconciliationJobDB.id == job_id,
                    SpeechReconciliationJobDB.tenant_id == tenant_id,
                    SpeechReconciliationJobDB.owner_subject == owner_subject,
                )
            ).first()
            return _job(row) if row is not None else None

    def list_jobs(
        self,
        *,
        tenant_id: str,
        owner_subject: str,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[SpeechReconciliationJobRecord, ...]:
        if not 0 <= offset <= 1_000_000 or not 1 <= limit <= 200:
            raise SpeechReconciliationRepositoryError("speech_reconciliation_pagination_invalid", status_code=422)
        with Session(engine) as session:
            rows = session.exec(
                select(SpeechReconciliationJobDB)
                .where(
                    SpeechReconciliationJobDB.tenant_id == tenant_id,
                    SpeechReconciliationJobDB.owner_subject == owner_subject,
                )
                .order_by(SpeechReconciliationJobDB.created_at_ms.desc(), SpeechReconciliationJobDB.id)
                .offset(offset)
                .limit(limit)
            ).all()
            return tuple(_job(row) for row in rows)

    def transition(
        self,
        *,
        tenant_id: str,
        owner_subject: str,
        job_id: str,
        target_state: str,
        stage: str,
        reason_code: str,
        expected_version: int,
        idempotency_key_digest: str,
        request_digest: str,
        now_ms: int | None = None,
    ) -> SpeechReconciliationMutationResult:
        guard = _SQLITE_WRITE_GUARD if engine.dialect.name == "sqlite" else nullcontext()
        with guard:
            return self._transition(
                tenant_id=tenant_id,
                owner_subject=owner_subject,
                job_id=job_id,
                target_state=target_state,
                stage=stage,
                reason_code=reason_code,
                expected_version=expected_version,
                idempotency_key_digest=idempotency_key_digest,
                request_digest=request_digest,
                now_ms=now_ms,
            )

    def _transition(
        self,
        *,
        tenant_id: str,
        owner_subject: str,
        job_id: str,
        target_state: str,
        stage: str,
        reason_code: str,
        expected_version: int,
        idempotency_key_digest: str,
        request_digest: str,
        now_ms: int | None = None,
    ) -> SpeechReconciliationMutationResult:
        now = _now(now_ms)
        operation = _transition_operation(target_state)
        _require_mutation_digest(idempotency_key_digest, "speech_reconciliation_idempotency_digest_invalid")
        _require_mutation_digest(request_digest, "speech_reconciliation_request_digest_invalid")
        with Session(engine) as session:
            row = _locked_job(session, tenant_id, owner_subject, job_id)
            existing = _mutation_receipt(
                session,
                tenant_id=tenant_id,
                owner_subject=owner_subject,
                job_id=job_id,
                operation=operation,
                idempotency_key_digest=idempotency_key_digest,
            )
            if existing is not None:
                return _replay_mutation(existing, request_digest=request_digest)
            transition = self._states.transition(row.state, target_state, stage=stage, reason_code=reason_code)
            if transition.duplicate:
                receipt = _new_mutation_receipt(
                    row=row,
                    operation=operation,
                    idempotency_key_digest=idempotency_key_digest,
                    request_digest=request_digest,
                    now_ms=now,
                    state_changed=False,
                )
                session.add(receipt)
                try:
                    session.commit()
                except IntegrityError as exc:
                    session.rollback()
                    return _mutation_winner_or_raise(
                        session,
                        tenant_id=tenant_id,
                        owner_subject=owner_subject,
                        job_id=job_id,
                        operation=operation,
                        idempotency_key_digest=idempotency_key_digest,
                        request_digest=request_digest,
                        cause=exc,
                    )
                return _result_from_receipt(receipt, applied=False)
            if row.version != expected_version:
                raise SpeechReconciliationRepositoryError("speech_reconciliation_version_stale")
            attempt = None
            affected_attempt_id = None
            affected_fencing_epoch = None
            if target_state in {"paused", "cancel_requested", "cancelled"} and row.active_attempt_id:
                affected_attempt_id = row.active_attempt_id
                affected_fencing_epoch = row.fencing_epoch
                attempt = session.exec(
                    select(SpeechReconciliationAttemptDB)
                    .where(
                        SpeechReconciliationAttemptDB.id == row.active_attempt_id,
                        SpeechReconciliationAttemptDB.job_id == row.id,
                    )
                    .with_for_update()
                ).first()
                if attempt is not None and attempt.state == "running":
                    attempt.state = "cancel_requested" if target_state == "cancel_requested" else "fenced"
                    attempt.version += 1
                    attempt.updated_at_ms = now
                    attempt.finished_at_ms = now
                    session.add(attempt)
                # Clearing the active pointer is the publication fence. A
                # resume must mint a new attempt and a higher fencing epoch.
                row.active_attempt_id = None
            row.state = target_state
            row.stage = stage
            row.reason_code = reason_code
            row.version += 1
            row.updated_at_ms = now
            if target_state in {"completed", "dataset_only_completed", "failed", "cancelled", "expired"}:
                row.finished_at_ms = now
            session.add(row)
            receipt = _new_mutation_receipt(
                row=row,
                operation=operation,
                idempotency_key_digest=idempotency_key_digest,
                request_digest=request_digest,
                now_ms=now,
                state_changed=True,
                affected_attempt_id=affected_attempt_id,
                affected_fencing_epoch=affected_fencing_epoch,
            )
            session.add(receipt)
            self._enqueue_audit(
                session,
                self._audit_event(
                    tenant_id=tenant_id,
                    job_id=job_id,
                    idempotency_key=(
                        f"speech-reconciliation:mutation:{job_id}:{operation}:"
                        f"{idempotency_key_digest}"
                    ),
                    event_type="semantic_job",
                    transition=target_state,
                    reason_code=reason_code,
                    epoch=max(1, row.fencing_epoch),
                    lease_ref=affected_attempt_id,
                ),
            )
            try:
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                return _mutation_winner_or_raise(
                    session,
                    tenant_id=tenant_id,
                    owner_subject=owner_subject,
                    job_id=job_id,
                    operation=operation,
                    idempotency_key_digest=idempotency_key_digest,
                    request_digest=request_digest,
                    cause=exc,
                )
            session.refresh(row)
            return _result_from_receipt(receipt, applied=True)

    def reduce_factor(
        self,
        *,
        tenant_id: str,
        owner_subject: str,
        job_id: str,
        max_compute_factor: int,
        expected_version: int,
        reason_code: str,
        idempotency_key_digest: str,
        request_digest: str,
        now_ms: int | None = None,
    ) -> SpeechReconciliationMutationResult:
        """Atomically reduce future work; already consumed ledger units remain immutable."""

        guard = _SQLITE_WRITE_GUARD if engine.dialect.name == "sqlite" else nullcontext()
        with guard:
            return self._reduce_factor(
                tenant_id=tenant_id,
                owner_subject=owner_subject,
                job_id=job_id,
                max_compute_factor=max_compute_factor,
                expected_version=expected_version,
                reason_code=reason_code,
                idempotency_key_digest=idempotency_key_digest,
                request_digest=request_digest,
                now_ms=now_ms,
            )

    def _reduce_factor(
        self,
        *,
        tenant_id: str,
        owner_subject: str,
        job_id: str,
        max_compute_factor: int,
        expected_version: int,
        reason_code: str,
        idempotency_key_digest: str,
        request_digest: str,
        now_ms: int | None = None,
    ) -> SpeechReconciliationMutationResult:
        now = _now(now_ms)
        if not 1 <= max_compute_factor <= 100:
            raise SpeechReconciliationRepositoryError("speech_reconciliation_factor_invalid", status_code=422)
        reason = str(reason_code or "").strip()
        if not reason or len(reason) > 128 or any(character.isspace() for character in reason):
            raise SpeechReconciliationRepositoryError("speech_reconciliation_reason_invalid", status_code=422)
        _require_mutation_digest(idempotency_key_digest, "speech_reconciliation_idempotency_digest_invalid")
        _require_mutation_digest(request_digest, "speech_reconciliation_request_digest_invalid")
        with Session(engine) as session:
            row = _locked_job(session, tenant_id, owner_subject, job_id)
            existing = _mutation_receipt(
                session,
                tenant_id=tenant_id,
                owner_subject=owner_subject,
                job_id=job_id,
                operation="reduce",
                idempotency_key_digest=idempotency_key_digest,
            )
            if existing is not None:
                return _replay_mutation(existing, request_digest=request_digest)
            if row.state not in {"queued", "paused", "running"}:
                raise SpeechReconciliationRepositoryError("speech_reconciliation_factor_state_invalid")
            if row.version != expected_version:
                raise SpeechReconciliationRepositoryError("speech_reconciliation_version_stale")
            if max_compute_factor > row.max_compute_factor:
                raise SpeechReconciliationRepositoryError("speech_reconciliation_factor_increase_forbidden")
            if max_compute_factor == row.max_compute_factor:
                receipt = _new_mutation_receipt(
                    row=row,
                    operation="reduce",
                    idempotency_key_digest=idempotency_key_digest,
                    request_digest=request_digest,
                    now_ms=now,
                    state_changed=False,
                )
                session.add(receipt)
                try:
                    session.commit()
                except IntegrityError as exc:
                    session.rollback()
                    return _mutation_winner_or_raise(
                        session,
                        tenant_id=tenant_id,
                        owner_subject=owner_subject,
                        job_id=job_id,
                        operation="reduce",
                        idempotency_key_digest=idempotency_key_digest,
                        request_digest=request_digest,
                        cause=exc,
                    )
                return _result_from_receipt(receipt, applied=False)
            row.max_compute_factor = max_compute_factor
            row.current_compute_factor = min(row.current_compute_factor, max_compute_factor)
            row.reason_code = reason
            row.version += 1
            row.updated_at_ms = now
            session.add(row)
            receipt = _new_mutation_receipt(
                row=row,
                operation="reduce",
                idempotency_key_digest=idempotency_key_digest,
                request_digest=request_digest,
                now_ms=now,
                state_changed=True,
            )
            session.add(receipt)
            self._enqueue_audit(
                session,
                self._audit_event(
                    tenant_id=tenant_id,
                    job_id=job_id,
                    idempotency_key=(
                        f"speech-reconciliation:mutation:{job_id}:reduce:"
                        f"{idempotency_key_digest}"
                    ),
                    event_type="semantic_budget",
                    transition="factor_reduced",
                    reason_code=reason,
                    epoch=max(1, row.fencing_epoch),
                    lease_ref=row.active_attempt_id,
                ),
            )
            try:
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                return _mutation_winner_or_raise(
                    session,
                    tenant_id=tenant_id,
                    owner_subject=owner_subject,
                    job_id=job_id,
                    operation="reduce",
                    idempotency_key_digest=idempotency_key_digest,
                    request_digest=request_digest,
                    cause=exc,
                )
            session.refresh(row)
            return _result_from_receipt(receipt, applied=True)

    def claim_attempt(
        self,
        *,
        tenant_id: str,
        owner_subject: str,
        job_id: str,
        expected_job_version: int,
        worker_id_digest: str,
        worker_capability_digest: str,
        location_digest: str,
        resource_profile_digest: str,
        fencing_token_digest: str,
        lease_expires_at_ms: int,
        now_ms: int | None = None,
    ) -> SpeechReconciliationAttemptRecord:
        now = _now(now_ms)
        with Session(engine) as session:
            job = _locked_job(session, tenant_id, owner_subject, job_id)
            if job.version != expected_job_version or job.state not in {"queued", "running"}:
                raise SpeechReconciliationRepositoryError("speech_reconciliation_claim_stale")
            active = None
            if job.active_attempt_id:
                active = session.get(SpeechReconciliationAttemptDB, job.active_attempt_id)
            if active is not None and active.state == "running" and active.lease_expires_at_ms > now:
                raise SpeechReconciliationRepositoryError("speech_reconciliation_attempt_already_claimed")
            attempt_number = (
                int(
                    session.exec(
                        select(func.count(SpeechReconciliationAttemptDB.id)).where(
                            SpeechReconciliationAttemptDB.job_id == job.id
                        )
                    ).one()
                )
                + 1
            )
            epoch = job.fencing_epoch + 1
            attempt = SpeechReconciliationAttemptDB(
                job_id=job.id,
                tenant_id=tenant_id,
                owner_subject=owner_subject,
                attempt_number=attempt_number,
                worker_id_digest=worker_id_digest,
                worker_capability_digest=worker_capability_digest,
                location_digest=location_digest,
                resource_profile_digest=resource_profile_digest,
                fencing_token_digest=fencing_token_digest,
                fencing_epoch=epoch,
                lease_expires_at_ms=min(lease_expires_at_ms, job.deadline_at_ms),
                deadline_at_ms=job.deadline_at_ms,
                last_heartbeat_at_ms=now,
                created_at_ms=now,
                updated_at_ms=now,
            )
            if attempt.lease_expires_at_ms <= now:
                raise SpeechReconciliationRepositoryError("speech_reconciliation_lease_invalid", status_code=422)
            if active is not None and active.state == "running":
                active.state = "fenced"
                active.finished_at_ms = now
                active.updated_at_ms = now
                active.version += 1
                session.add(active)
            session.add(attempt)
            session.flush()
            job.active_attempt_id = attempt.id
            job.fencing_epoch = epoch
            job.state = "running"
            job.stage = "staging"
            job.reason_code = "speech_reconciliation_attempt_claimed"
            job.version += 1
            job.updated_at_ms = now
            session.add(job)
            if active is not None and active.state == "fenced":
                self._enqueue_audit(
                    session,
                    self._audit_event(
                        tenant_id=tenant_id,
                        job_id=job_id,
                        idempotency_key=f"speech-reconciliation:replaced-fence:{active.id}:{active.version}",
                        event_type="semantic_lease",
                        transition="fenced",
                        reason_code="speech_reconciliation_stale_attempt_replaced",
                        epoch=active.fencing_epoch,
                        lease_ref=active.id,
                    ),
                )
            self._enqueue_audit(
                session,
                self._audit_event(
                    tenant_id=tenant_id,
                    job_id=job_id,
                    idempotency_key=f"speech-reconciliation:claim:{attempt.id}:{epoch}",
                    event_type="semantic_lease",
                    transition="acquired",
                    reason_code="speech_reconciliation_attempt_claimed",
                    epoch=epoch,
                    lease_ref=attempt.id,
                ),
            )
            self._enqueue_audit(
                session,
                self._audit_event(
                    tenant_id=tenant_id,
                    job_id=job_id,
                    idempotency_key=f"speech-reconciliation:running:{job_id}:{epoch}",
                    event_type="semantic_job",
                    transition="running",
                    reason_code="speech_reconciliation_attempt_claimed",
                    epoch=epoch,
                    lease_ref=attempt.id,
                ),
            )
            session.commit()
            session.refresh(attempt)
            return _attempt(attempt)

    def heartbeat(
        self,
        *,
        job_id: str,
        attempt_id: str,
        fencing_epoch: int,
        fencing_token_digest: str,
        expected_version: int,
        lease_expires_at_ms: int,
        now_ms: int | None = None,
    ) -> SpeechReconciliationAttemptRecord:
        now = _now(now_ms)
        with Session(engine) as session:
            attempt = session.exec(
                select(SpeechReconciliationAttemptDB)
                .where(SpeechReconciliationAttemptDB.id == attempt_id, SpeechReconciliationAttemptDB.job_id == job_id)
                .with_for_update()
            ).first()
            if attempt is None:
                raise SpeechReconciliationRepositoryError("speech_reconciliation_attempt_not_found", status_code=404)
            if (
                attempt.state != "running"
                or attempt.version != expected_version
                or attempt.fencing_epoch != fencing_epoch
                or attempt.fencing_token_digest != fencing_token_digest
                or attempt.lease_expires_at_ms <= now
            ):
                raise SpeechReconciliationRepositoryError("speech_reconciliation_fence_stale")
            attempt.last_heartbeat_at_ms = now
            attempt.lease_expires_at_ms = min(lease_expires_at_ms, attempt.deadline_at_ms)
            attempt.version += 1
            attempt.updated_at_ms = now
            session.add(attempt)
            self._enqueue_audit(
                session,
                self._audit_event(
                    tenant_id=attempt.tenant_id,
                    job_id=job_id,
                    idempotency_key=f"speech-reconciliation:heartbeat:{attempt_id}:{attempt.version}",
                    event_type="semantic_lease",
                    transition="renewed",
                    reason_code="speech_reconciliation_heartbeat_accepted",
                    epoch=fencing_epoch,
                    lease_ref=attempt_id,
                ),
            )
            session.commit()
            session.refresh(attempt)
            return _attempt(attempt)

    def list_collectible_attempts(
        self,
        *,
        now_ms: int,
        limit: int = 100,
    ) -> tuple[SpeechReconciliationCollectibleAttempt, ...]:
        """Return only currently fenced attempts that may need poll/cancel work."""

        if not 1 <= limit <= 1000:
            raise ValueError("speech_reconciliation_collector_batch_invalid")
        with Session(engine) as session:
            pairs = session.exec(
                select(SpeechReconciliationJobDB, SpeechReconciliationAttemptDB)
                .join(
                    SpeechReconciliationAttemptDB,
                    SpeechReconciliationAttemptDB.job_id == SpeechReconciliationJobDB.id,
                )
                .where(
                    SpeechReconciliationJobDB.state.in_(["running", "cancel_requested"]),
                    SpeechReconciliationAttemptDB.state.in_(["running", "cancel_requested"]),
                    SpeechReconciliationAttemptDB.deadline_at_ms > now_ms,
                )
                .order_by(
                    SpeechReconciliationAttemptDB.last_heartbeat_at_ms.asc(),
                    SpeechReconciliationAttemptDB.id.asc(),
                )
                .limit(limit)
            ).all()
        projected: list[SpeechReconciliationCollectibleAttempt] = []
        for job, attempt in pairs:
            if job.state == "running" and job.active_attempt_id != attempt.id:
                continue
            projected.append(
                SpeechReconciliationCollectibleAttempt(
                    tenant_id=job.tenant_id,
                    owner_subject=job.owner_subject,
                    job_state=job.state,
                    job_contract=_active_job_contract(job, attempt),
                    attempt_version=attempt.version,
                    lease_expires_at_ms=attempt.lease_expires_at_ms,
                )
            )
        return tuple(projected)

    def pause_active_attempt(
        self,
        *,
        tenant_id: str,
        owner_subject: str,
        job_id: str,
        attempt_id: str,
        fencing_epoch: int,
        reason_code: str,
        now_ms: int | None = None,
    ) -> bool:
        """Atomically fence one attempt and pause its job under Hub authority."""

        reason = str(reason_code or "").strip()
        if not reason.startswith("speech_reconciliation_") or len(reason) > 128:
            raise SpeechReconciliationRepositoryError(
                "speech_reconciliation_reason_invalid",
                status_code=422,
            )
        now = _now(now_ms)
        with Session(engine) as session:
            job = session.exec(
                select(SpeechReconciliationJobDB)
                .where(
                    SpeechReconciliationJobDB.id == job_id,
                    SpeechReconciliationJobDB.tenant_id == tenant_id,
                    SpeechReconciliationJobDB.owner_subject == owner_subject,
                )
                .with_for_update()
            ).first()
            attempt = session.exec(
                select(SpeechReconciliationAttemptDB)
                .where(
                    SpeechReconciliationAttemptDB.id == attempt_id,
                    SpeechReconciliationAttemptDB.job_id == job_id,
                    SpeechReconciliationAttemptDB.tenant_id == tenant_id,
                    SpeechReconciliationAttemptDB.owner_subject == owner_subject,
                )
                .with_for_update()
            ).first()
            if job is None or attempt is None:
                return False
            if job.state == "paused" and attempt.state == "fenced":
                return True
            if (
                job.state != "running"
                or attempt.state != "running"
                or attempt.fencing_epoch != fencing_epoch
                or job.active_attempt_id != attempt.id
            ):
                return False
            self._states.transition(
                job.state,
                "paused",
                stage=job.stage,
                reason_code=reason,
            )
            attempt.state = "fenced"
            attempt.version += 1
            attempt.updated_at_ms = now
            attempt.finished_at_ms = now
            job.active_attempt_id = None
            job.state = "paused"
            job.reason_code = reason
            job.version += 1
            job.updated_at_ms = now
            session.add(attempt)
            session.add(job)
            self._enqueue_audit(
                session,
                self._audit_event(
                    tenant_id=tenant_id,
                    job_id=job_id,
                    idempotency_key=f"speech-reconciliation:pause:{job_id}:{job.version}",
                    event_type="semantic_job",
                    transition="paused",
                    reason_code=reason,
                    epoch=fencing_epoch,
                    lease_ref=attempt_id,
                ),
            )
            session.commit()
            return True

    def cancel_active_attempt(
        self,
        *,
        tenant_id: str,
        owner_subject: str,
        job_id: str,
        attempt_id: str,
        fencing_epoch: int,
        reason_code: str,
        now_ms: int | None = None,
    ) -> bool:
        """Atomically finish an explicitly cancelled attempt and job."""

        reason = str(reason_code or "").strip()
        if not reason.startswith("speech_reconciliation_") or len(reason) > 128:
            raise SpeechReconciliationRepositoryError(
                "speech_reconciliation_reason_invalid",
                status_code=422,
            )
        now = _now(now_ms)
        with Session(engine) as session:
            job = session.exec(
                select(SpeechReconciliationJobDB)
                .where(
                    SpeechReconciliationJobDB.id == job_id,
                    SpeechReconciliationJobDB.tenant_id == tenant_id,
                    SpeechReconciliationJobDB.owner_subject == owner_subject,
                )
                .with_for_update()
            ).first()
            attempt = session.exec(
                select(SpeechReconciliationAttemptDB)
                .where(
                    SpeechReconciliationAttemptDB.id == attempt_id,
                    SpeechReconciliationAttemptDB.job_id == job_id,
                    SpeechReconciliationAttemptDB.tenant_id == tenant_id,
                    SpeechReconciliationAttemptDB.owner_subject == owner_subject,
                )
                .with_for_update()
            ).first()
            if job is None or attempt is None:
                return False
            if job.state == "cancelled" and attempt.state == "cancelled":
                return True
            if (
                job.state != "cancel_requested"
                or attempt.state not in {"running", "cancel_requested"}
                or attempt.fencing_epoch != fencing_epoch
                or job.active_attempt_id not in {None, attempt.id}
            ):
                return False
            self._states.transition(
                job.state,
                "cancelled",
                stage=job.stage,
                reason_code=reason,
            )
            attempt.state = "cancelled"
            attempt.version += 1
            attempt.updated_at_ms = now
            attempt.finished_at_ms = now
            job.active_attempt_id = None
            job.state = "cancelled"
            job.reason_code = reason
            job.version += 1
            job.updated_at_ms = now
            job.finished_at_ms = now
            session.add(attempt)
            session.add(job)
            self._enqueue_audit(
                session,
                self._audit_event(
                    tenant_id=tenant_id,
                    job_id=job_id,
                    idempotency_key=f"speech-reconciliation:cancel:{job_id}:{job.version}",
                    event_type="semantic_job",
                    transition="cancelled",
                    reason_code=reason,
                    epoch=fencing_epoch,
                    lease_ref=attempt_id,
                ),
            )
            session.commit()
            return True

    def save_checkpoint(
        self,
        *,
        tenant_id: str,
        owner_subject: str,
        job_contract,
        checkpoint: SpeechReconciliationCheckpoint,
        now_ms: int | None = None,
    ) -> SpeechReconciliationAttemptRecord:
        assert_result_matches_job(job_contract, checkpoint)
        now = _now(now_ms)
        with Session(engine) as session:
            job = _locked_job(session, tenant_id, owner_subject, job_contract.job_id)
            attempt = _locked_attempt(session, job.id, checkpoint.attempt_id)
            _require_current_fence(job, attempt, checkpoint.fencing_epoch, checkpoint.fencing_token_digest, now)
            if checkpoint.ledger_sequence < job.ledger_sequence:
                raise SpeechReconciliationRepositoryError("speech_reconciliation_ledger_stale")
            existing = session.exec(
                select(SpeechReconciliationCheckpointDB).where(
                    SpeechReconciliationCheckpointDB.job_id == job.id,
                    SpeechReconciliationCheckpointDB.attempt_id == attempt.id,
                    SpeechReconciliationCheckpointDB.checkpoint_sequence == checkpoint.checkpoint_sequence,
                )
            ).first()
            if existing is not None:
                if existing.checkpoint_digest != checkpoint.checkpoint_digest:
                    raise SpeechReconciliationRepositoryError("speech_reconciliation_checkpoint_conflict")
                return _attempt(attempt)
            if checkpoint.checkpoint_sequence != attempt.checkpoint_sequence + 1:
                raise SpeechReconciliationRepositoryError("speech_reconciliation_checkpoint_sequence_stale")
            session.add(
                SpeechReconciliationCheckpointDB(
                    job_id=job.id,
                    attempt_id=attempt.id,
                    tenant_id=tenant_id,
                    owner_subject=owner_subject,
                    fencing_epoch=checkpoint.fencing_epoch,
                    consent_version=checkpoint.consent_version,
                    revocation_epoch=checkpoint.revocation_epoch,
                    input_manifest_digest=checkpoint.input_manifest_digest,
                    policy_digest=checkpoint.policy_digest,
                    ledger_sequence=checkpoint.ledger_sequence,
                    key_epoch=checkpoint.key_epoch,
                    checkpoint_sequence=checkpoint.checkpoint_sequence,
                    checkpoint_digest=checkpoint.checkpoint_digest,
                    checkpoint_ref=checkpoint.checkpoint_ref,
                    stage=checkpoint.stage,
                    state_digest=checkpoint.state_digest,
                    created_at_ms=now,
                )
            )
            attempt.checkpoint_sequence = checkpoint.checkpoint_sequence
            attempt.checkpoint_digest = checkpoint.checkpoint_digest
            attempt.checkpoint_ref = checkpoint.checkpoint_ref
            attempt.version += 1
            attempt.updated_at_ms = now
            job.checkpoint_count += 1
            job.ledger_sequence = checkpoint.ledger_sequence
            job.stage = checkpoint.stage
            job.version += 1
            job.updated_at_ms = now
            session.add(attempt)
            session.add(job)
            self._enqueue_audit(
                session,
                self._audit_event(
                    tenant_id=tenant_id,
                    job_id=job.id,
                    idempotency_key=(
                        f"speech-reconciliation:checkpoint:{job.id}:"
                        f"{attempt.id}:{checkpoint.checkpoint_sequence}:{checkpoint.checkpoint_digest}"
                    ),
                    event_type="semantic_job",
                    transition="checkpointed",
                    reason_code="speech_reconciliation_checkpoint_admitted",
                    epoch=checkpoint.fencing_epoch,
                    lease_ref=attempt.id,
                ),
            )
            session.commit()
            session.refresh(attempt)
            return _attempt(attempt)

    def apply_quality_decision(
        self,
        *,
        tenant_id: str,
        owner_subject: str,
        job_contract: SpeechReconciliationJob,
        action: str,
        current_factor: int,
        next_factor: int,
        quality_score_micros: int,
        unresolved_count: int,
        unresolved_high_quality_conflicts: int,
        reason_code: str,
        now_ms: int | None = None,
    ) -> SpeechReconciliationJobRecord:
        """Persist one Hub policy observation and optionally fence/requeue a wave.

        The worker can only report a closed outcome.  This mutation is the
        sole production authority that may turn that observation into another
        attempt, and its audit event shares the database transaction.
        """

        if action not in {"extend", "stop", "dataset_only"}:
            raise SpeechReconciliationRepositoryError("speech_reconciliation_quality_action_invalid")
        if (
            type(current_factor) is not int
            or type(next_factor) is not int
            or not 1 <= current_factor <= job_contract.max_compute_factor
            or not 1 <= next_factor <= job_contract.max_compute_factor
            or not 0 <= quality_score_micros <= 1_000_000
            or not 0 <= unresolved_count <= 1_000_000
            or not 0 <= unresolved_high_quality_conflicts <= 1_000_000
            or unresolved_high_quality_conflicts > unresolved_count
        ):
            raise SpeechReconciliationRepositoryError("speech_reconciliation_quality_observation_invalid")
        if action == "extend" and next_factor <= current_factor:
            raise SpeechReconciliationRepositoryError("speech_reconciliation_quality_extension_invalid")
        reason = str(reason_code or "").strip()
        if not reason.startswith("speech_reconciliation_") or len(reason) > 128:
            raise SpeechReconciliationRepositoryError("speech_reconciliation_reason_invalid")
        now = _now(now_ms)
        observation = {
            "attempt_id": job_contract.attempt_id,
            "fencing_epoch": job_contract.fencing_epoch,
            "factor": current_factor,
            "quality_score_micros": quality_score_micros,
            "unresolved_count": unresolved_count,
            "unresolved_high_quality_conflicts": unresolved_high_quality_conflicts,
            "action": action,
            "next_factor": next_factor,
            "reason_code": reason,
        }
        with Session(engine) as session:
            job = _locked_job(session, tenant_id, owner_subject, job_contract.job_id)
            attempt = _locked_attempt(session, job.id, job_contract.attempt_id)
            history = [dict(value) for value in (job.quality_history or []) if isinstance(value, Mapping)]
            previous = next(
                (value for value in history if value.get("attempt_id") == job_contract.attempt_id),
                None,
            )
            if previous is not None:
                if previous != observation:
                    raise SpeechReconciliationRepositoryError("speech_reconciliation_quality_decision_conflict")
                return _job(job)
            _require_current_fence(
                job,
                attempt,
                job_contract.fencing_epoch,
                job_contract.fencing_token_digest,
                now,
            )
            if job.current_compute_factor != current_factor:
                raise SpeechReconciliationRepositoryError("speech_reconciliation_quality_factor_stale")
            if len(history) >= 16:
                raise SpeechReconciliationRepositoryError("speech_reconciliation_quality_history_limit")
            history.append(observation)
            job.quality_history = history
            job.unresolved_count = unresolved_count
            job.reason_code = reason
            job.version += 1
            job.updated_at_ms = now
            transition = "quality_stopped"
            if action == "extend":
                attempt.state = "fenced"
                attempt.finished_at_ms = now
                attempt.updated_at_ms = now
                attempt.version += 1
                job.state = "queued"
                job.stage = "slow_asr"
                job.active_attempt_id = None
                job.current_compute_factor = next_factor
                transition = "quality_extended"
                session.add(attempt)
            session.add(job)
            self._enqueue_audit(
                session,
                self._audit_event(
                    tenant_id=tenant_id,
                    job_id=job.id,
                    idempotency_key=(
                        f"speech-reconciliation:quality:{job.id}:"
                        f"{job_contract.attempt_id}:{action}:{quality_score_micros}"
                    ),
                    event_type="semantic_job",
                    transition=transition,
                    reason_code=reason,
                    epoch=job_contract.fencing_epoch,
                    lease_ref=job_contract.attempt_id,
                ),
            )
            session.commit()
            session.refresh(job)
            return _job(job)

    def complete(
        self,
        *,
        tenant_id: str,
        owner_subject: str,
        job_contract,
        result: SpeechReconciliationResult,
        publication_authorized: bool,
        now_ms: int | None = None,
    ) -> SpeechReconciliationJobRecord:
        assert_result_matches_job(job_contract, result)
        publishes_dataset = result.status in {"completed", "dataset_only_completed"}
        if publishes_dataset and not publication_authorized:
            raise SpeechReconciliationRepositoryError("speech_reconciliation_budget_publication_denied")
        now = _now(now_ms)
        with Session(engine) as session:
            job = _locked_job(session, tenant_id, owner_subject, job_contract.job_id)
            attempt = _locked_attempt(session, job.id, result.attempt_id)
            _require_current_fence(job, attempt, result.fencing_epoch, result.fencing_token_digest, now)
            if result.ledger_sequence < job.ledger_sequence:
                raise SpeechReconciliationRepositoryError("speech_reconciliation_ledger_stale")
            self._states.transition(
                job.state,
                result.status,
                stage="finalization",
                reason_code=result.reason_code,
            )
            job.state = result.status
            job.stage = "finalization"
            job.reason_code = result.reason_code
            job.ledger_sequence = result.ledger_sequence
            job.resolved_count = result.resolved_count
            job.unresolved_count = result.unresolved_count
            job.rejected_count = result.rejected_count
            job.quarantined_count = result.quarantined_count
            job.version += 1
            job.updated_at_ms = now
            job.finished_at_ms = now
            attempt.state = "completed" if result.status in {"completed", "dataset_only_completed"} else result.status
            attempt.finished_at_ms = now
            attempt.updated_at_ms = now
            attempt.version += 1
            session.add(job)
            session.add(attempt)
            nodes = [SpeechLineageNode("reconciliation", job.request_digest)]
            edges: list[SpeechLineageEdge] = []
            if result.dataset_manifest_digest and result.dataset_artifact_ref:
                session.add(
                    SpeechReconciliationArtifactDB(
                        job_id=job.id,
                        tenant_id=tenant_id,
                        owner_subject=owner_subject,
                        artifact_kind="manifest",
                        artifact_digest=result.dataset_manifest_digest,
                        artifact_ref=result.dataset_artifact_ref,
                        consent_version=result.consent_version,
                        revocation_epoch=result.revocation_epoch,
                        key_epoch=result.key_epoch,
                        created_at_ms=now,
                    )
                )
                nodes.append(SpeechLineageNode("manifest", result.dataset_manifest_digest))
                edges.append(
                    SpeechLineageEdge(
                        "reconciliation",
                        job.request_digest,
                        "manifest",
                        result.dataset_manifest_digest,
                        "materialized_as",
                    )
                )
            for kind, digest in (
                ("checkpoint", result.checkpoint_digest),
                ("evaluation", result.evaluation_digest),
                ("adapter", result.adapter_digest),
            ):
                if digest:
                    nodes.append(SpeechLineageNode(kind, digest))
                    edges.append(SpeechLineageEdge("reconciliation", job.request_digest, kind, digest, "produced"))
            outbox = self._lineage.stage(
                session,
                tenant_id=tenant_id,
                owner_subject=owner_subject,
                nodes=tuple(nodes),
                edges=tuple(edges),
                now_ms=now,
            )
            self._enqueue_audit(
                session,
                self._audit_event(
                    tenant_id=tenant_id,
                    job_id=job.id,
                    idempotency_key=f"speech-reconciliation:result:{job.id}:{result.fencing_epoch}:{result.status}",
                    event_type="semantic_job",
                    transition=result.status,
                    reason_code=result.reason_code,
                    epoch=result.fencing_epoch,
                    lease_ref=attempt.id,
                ),
            )
            session.commit()
            session.refresh(job)
        self._lineage.process_outbox(event_digest=outbox, tenant_id=tenant_id, owner_subject=owner_subject)
        return _job(job)

    def expire_stale_attempts(self, *, now_ms: int | None = None, limit: int = 100) -> tuple[str, ...]:
        now = _now(now_ms)
        if not 1 <= limit <= 1000:
            raise ValueError("speech reconciliation recovery limit invalid")
        expired: list[str] = []
        with Session(engine) as session:
            attempts = session.exec(
                select(SpeechReconciliationAttemptDB)
                .where(
                    SpeechReconciliationAttemptDB.state == "running",
                    SpeechReconciliationAttemptDB.lease_expires_at_ms <= now,
                )
                .order_by(SpeechReconciliationAttemptDB.lease_expires_at_ms)
                .limit(limit)
                .with_for_update()
            ).all()
            for attempt in attempts:
                attempt.state = "fenced"
                attempt.version += 1
                attempt.updated_at_ms = now
                attempt.finished_at_ms = now
                session.add(attempt)
                job = session.get(SpeechReconciliationJobDB, attempt.job_id)
                transition = "fenced"
                reason_code = "speech_reconciliation_stale_attempt_fenced"
                if job is not None and job.active_attempt_id == attempt.id and job.state == "running":
                    job.state = "queued" if job.deadline_at_ms > now else "expired"
                    job.reason_code = (
                        "speech_reconciliation_stale_attempt_requeued"
                        if job.deadline_at_ms > now
                        else "speech_reconciliation_deadline_expired"
                    )
                    job.active_attempt_id = None
                    job.version += 1
                    job.updated_at_ms = now
                    if job.state == "expired":
                        job.finished_at_ms = now
                    session.add(job)
                    transition = job.state
                    reason_code = job.reason_code
                self._enqueue_audit(
                    session,
                    self._audit_event(
                        tenant_id=attempt.tenant_id,
                        job_id=attempt.job_id,
                        idempotency_key=f"speech-reconciliation:expired-attempt:{attempt.id}:{attempt.version}",
                        event_type="semantic_recovery",
                        transition=transition,
                        reason_code=reason_code,
                        epoch=attempt.fencing_epoch,
                        lease_ref=attempt.id,
                    ),
                )
                expired.append(attempt.id)
            session.commit()
        return tuple(expired)

    def fence_attempt(
        self,
        attempt_id: str,
        *,
        reason_code: str,
        authority: str = "hub",
        now_ms: int | None = None,
    ) -> bool:
        if authority != "hub":
            raise PermissionError("speech_reconciliation_hub_fence_authority_required")
        now = _now(now_ms)
        with Session(engine) as session:
            attempt = session.exec(
                select(SpeechReconciliationAttemptDB)
                .where(SpeechReconciliationAttemptDB.id == attempt_id)
                .with_for_update()
            ).first()
            if attempt is None:
                return False
            job = session.exec(
                select(SpeechReconciliationJobDB)
                .where(SpeechReconciliationJobDB.id == attempt.job_id)
                .with_for_update()
            ).first()
            if attempt.state in {"fenced", "cancelled", "completed", "failed"}:
                return True
            attempt.state = "fenced"
            attempt.version += 1
            attempt.updated_at_ms = now
            attempt.finished_at_ms = now
            session.add(attempt)
            if job is not None and job.active_attempt_id == attempt.id:
                job.active_attempt_id = None
                if job.state == "running":
                    job.state = "queued"
                job.reason_code = reason_code
                job.version += 1
                job.updated_at_ms = now
                session.add(job)
            self._enqueue_audit(
                session,
                self._audit_event(
                    tenant_id=attempt.tenant_id,
                    job_id=attempt.job_id,
                    idempotency_key=f"speech-reconciliation:fence:{attempt.id}:{attempt.version}",
                    event_type="semantic_lease",
                    transition="fenced",
                    reason_code=reason_code,
                    epoch=attempt.fencing_epoch,
                    lease_ref=attempt.id,
                ),
            )
            session.commit()
            return True

    def attempt_audit_binding(self, attempt_id: str) -> tuple[str, str, int, str] | None:
        """Return the minimum content-free scope needed for audit repair."""

        with Session(engine) as session:
            pair = session.exec(
                select(SpeechReconciliationAttemptDB, SpeechReconciliationJobDB)
                .join(
                    SpeechReconciliationJobDB,
                    SpeechReconciliationJobDB.id == SpeechReconciliationAttemptDB.job_id,
                )
                .where(SpeechReconciliationAttemptDB.id == attempt_id)
            ).first()
            if pair is None:
                return None
            attempt, job = pair
            return job.tenant_id, job.id, attempt.fencing_epoch, job.reason_code

    def list_recovery_candidates(self, *, now_ms: int, limit: int):
        """Project persisted recovery facts without exposing payload content."""

        if not 1 <= limit <= 1000:
            raise ValueError("speech_reconciliation_recovery_batch_invalid")
        from agent.db_models.speech_evidence import SpeechEvidenceConsentDB
        from agent.services.background.speech_reconciliation_reconciler import (
            SpeechReconciliationRecoveryCandidate,
        )

        with Session(engine) as session:
            pairs = session.exec(
                select(SpeechReconciliationAttemptDB, SpeechReconciliationJobDB)
                .join(
                    SpeechReconciliationJobDB,
                    SpeechReconciliationJobDB.id == SpeechReconciliationAttemptDB.job_id,
                )
                .where(
                    SpeechReconciliationAttemptDB.state.in_(["running", "cancel_requested"]),
                    SpeechReconciliationJobDB.state.in_(["running", "cancel_requested"]),
                )
                .order_by(SpeechReconciliationAttemptDB.updated_at_ms, SpeechReconciliationAttemptDB.id)
                .limit(limit)
            ).all()
            candidates = []
            for attempt, job in pairs:
                consent = session.exec(
                    select(SpeechEvidenceConsentDB).where(
                        SpeechEvidenceConsentDB.id == job.consent_id,
                        SpeechEvidenceConsentDB.tenant_id == job.tenant_id,
                        SpeechEvidenceConsentDB.owner_subject == job.owner_subject,
                    )
                ).first()
                if (
                    consent is None
                    or consent.state != "active"
                    or consent.expires_at_ms <= now_ms
                    or consent.consent_version != job.consent_version
                    or consent.revocation_epoch != job.revocation_epoch
                ):
                    condition = "consent_revoked"
                elif job.deadline_at_ms <= now_ms:
                    condition = "job_expired"
                elif attempt.state == "cancel_requested" and attempt.updated_at_ms + 30_000 <= now_ms:
                    condition = "cancel_grace_elapsed"
                elif attempt.lease_expires_at_ms <= now_ms or attempt.last_heartbeat_at_ms + 30_000 <= now_ms:
                    condition = "stale_heartbeat"
                else:
                    continue
                candidates.append(
                    SpeechReconciliationRecoveryCandidate(
                        job_id=job.id,
                        attempt_id=attempt.id,
                        state=job.state,
                        stage=job.stage,
                        expected_version=job.version,
                        fencing_epoch=attempt.fencing_epoch,
                        retry_count=max(0, attempt.attempt_number - 1),
                        max_retries=2,
                        checkpoint_ref=attempt.checkpoint_ref,
                        condition=condition,
                    )
                )
            return tuple(candidates)

    def apply_recovery(self, candidate, action, *, authority: str) -> bool:
        if authority != "hub":
            raise PermissionError("speech_reconciliation_hub_recovery_authority_required")
        now = time.time_ns() // 1_000_000
        with Session(engine) as session:
            job = session.exec(
                select(SpeechReconciliationJobDB)
                .where(SpeechReconciliationJobDB.id == candidate.job_id)
                .with_for_update()
            ).first()
            attempt = session.exec(
                select(SpeechReconciliationAttemptDB)
                .where(
                    SpeechReconciliationAttemptDB.id == candidate.attempt_id,
                    SpeechReconciliationAttemptDB.job_id == candidate.job_id,
                )
                .with_for_update()
            ).first()
            if job is None or attempt is None:
                return False
            if (
                job.version != candidate.expected_version
                or attempt.fencing_epoch != candidate.fencing_epoch
                or attempt.state not in {"running", "cancel_requested"}
                or (action.resume_checkpoint_ref is not None and action.resume_checkpoint_ref != attempt.checkpoint_ref)
            ):
                return bool(
                    job.state == action.target_state
                    and job.active_attempt_id != attempt.id
                    and attempt.state != "running"
                )
            attempt.state = "cancelled" if action.target_state in {"cancelled", "expired"} else "fenced"
            attempt.version += 1
            attempt.updated_at_ms = now
            attempt.finished_at_ms = now
            job.active_attempt_id = None
            job.state = action.target_state
            job.reason_code = action.reason_code
            job.version += 1
            job.updated_at_ms = now
            if action.target_state in {"cancelled", "expired", "failed"}:
                job.finished_at_ms = now
            session.add(attempt)
            session.add(job)
            self._enqueue_audit(
                session,
                self._audit_event(
                    tenant_id=job.tenant_id,
                    job_id=job.id,
                    idempotency_key=f"speech-reconciliation:recovery:{attempt.id}:{job.version}",
                    event_type="semantic_recovery",
                    transition=action.target_state,
                    reason_code=action.reason_code,
                    epoch=attempt.fencing_epoch,
                    lease_ref=attempt.id,
                ),
            )
            session.commit()
            return True

    def latest_checkpoint_ref(
        self,
        *,
        tenant_id: str,
        owner_subject: str,
        job_id: str,
    ) -> tuple[str, str, int] | None:
        with Session(engine) as session:
            row = session.exec(
                select(SpeechReconciliationCheckpointDB)
                .where(
                    SpeechReconciliationCheckpointDB.job_id == job_id,
                    SpeechReconciliationCheckpointDB.tenant_id == tenant_id,
                    SpeechReconciliationCheckpointDB.owner_subject == owner_subject,
                )
                .order_by(
                    SpeechReconciliationCheckpointDB.created_at_ms.desc(),
                    SpeechReconciliationCheckpointDB.checkpoint_sequence.desc(),
                )
                .limit(1)
            ).first()
            if row is None:
                return None
            return row.checkpoint_ref, row.checkpoint_digest, row.checkpoint_sequence


class SqlSpeechReconciliationBudgetRepository:
    """Append-only ledger snapshots plus atomic job-sequence CAS."""

    def __init__(
        self,
        *,
        tenant_id: str,
        owner_subject: str,
        audit: SemanticMediaAuditPort | None = None,
    ) -> None:
        self._tenant_id = tenant_id
        self._owner_subject = owner_subject
        self._audit = audit

    @property
    def transactional_audit(self) -> bool:
        return self._audit is not None

    def get(self, *, job_id: str) -> SpeechReconciliationBudgetLedger | None:
        with Session(engine) as session:
            row = session.exec(
                select(SpeechReconciliationBudgetLedgerDB)
                .where(
                    SpeechReconciliationBudgetLedgerDB.job_id == job_id,
                    SpeechReconciliationBudgetLedgerDB.tenant_id == self._tenant_id,
                    SpeechReconciliationBudgetLedgerDB.owner_subject == self._owner_subject,
                )
                .order_by(SpeechReconciliationBudgetLedgerDB.sequence.desc())
                .limit(1)
            ).first()
            return _ledger(row) if row is not None else None

    def compare_and_swap(
        self,
        *,
        expected_sequence: int | None,
        ledger: SpeechReconciliationBudgetLedger,
        audit_event: SemanticMediaAuditEvent | None = None,
    ) -> bool:
        with Session(engine) as session:
            job = session.exec(
                select(SpeechReconciliationJobDB)
                .where(
                    SpeechReconciliationJobDB.id == ledger.job_id,
                    SpeechReconciliationJobDB.tenant_id == self._tenant_id,
                    SpeechReconciliationJobDB.owner_subject == self._owner_subject,
                )
                .with_for_update()
            ).first()
            if job is None:
                return False
            latest = session.exec(
                select(SpeechReconciliationBudgetLedgerDB.sequence)
                .where(SpeechReconciliationBudgetLedgerDB.job_id == ledger.job_id)
                .order_by(SpeechReconciliationBudgetLedgerDB.sequence.desc())
                .limit(1)
            ).first()
            if latest != expected_sequence:
                return False
            session.add(
                SpeechReconciliationBudgetLedgerDB(
                    job_id=ledger.job_id,
                    attempt_id=ledger.attempt_id,
                    tenant_id=self._tenant_id,
                    owner_subject=self._owner_subject,
                    fencing_epoch=ledger.fencing_epoch,
                    sequence=ledger.sequence,
                    stage=ledger.stage,
                    source_duration_ms=ledger.source_duration_ms,
                    compute_factor=ledger.compute_factor,
                    allocated=ledger.allocated.to_dict(),
                    reserved=ledger.reserved.to_dict(),
                    consumed=ledger.consumed.to_dict(),
                    remaining=ledger.remaining.to_dict(),
                )
            )
            job.ledger_sequence = ledger.sequence
            job.updated_at_ms = time.time_ns() // 1_000_000
            session.add(job)
            if audit_event is not None:
                SqlSemanticMediaAuditOutbox.enqueue_in_session(session, audit_event)
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                return False
            return True


def _locked_job(session: Session, tenant_id: str, owner_subject: str, job_id: str) -> SpeechReconciliationJobDB:
    row = session.exec(
        select(SpeechReconciliationJobDB)
        .where(
            SpeechReconciliationJobDB.id == job_id,
            SpeechReconciliationJobDB.tenant_id == tenant_id,
            SpeechReconciliationJobDB.owner_subject == owner_subject,
        )
        .with_for_update()
    ).first()
    if row is None:
        raise SpeechReconciliationRepositoryError("speech_reconciliation_job_not_found", status_code=404)
    return row


def _locked_attempt(session: Session, job_id: str, attempt_id: str) -> SpeechReconciliationAttemptDB:
    row = session.exec(
        select(SpeechReconciliationAttemptDB)
        .where(SpeechReconciliationAttemptDB.id == attempt_id, SpeechReconciliationAttemptDB.job_id == job_id)
        .with_for_update()
    ).first()
    if row is None:
        raise SpeechReconciliationRepositoryError("speech_reconciliation_attempt_not_found", status_code=404)
    return row


def _require_current_fence(job, attempt, epoch: int, token_digest: str, now_ms: int) -> None:
    if (
        job.active_attempt_id != attempt.id
        or job.fencing_epoch != epoch
        or attempt.fencing_epoch != epoch
        or attempt.fencing_token_digest != token_digest
        or attempt.state != "running"
        or attempt.lease_expires_at_ms <= now_ms
    ):
        raise SpeechReconciliationRepositoryError("speech_reconciliation_fence_stale")


def _job(row: SpeechReconciliationJobDB) -> SpeechReconciliationJobRecord:
    return SpeechReconciliationJobRecord(
        **{field: getattr(row, field) for field in SpeechReconciliationJobRecord.__dataclass_fields__}
    )


def _transition_operation(target_state: str) -> str:
    try:
        return {
            "paused": "pause",
            "queued": "resume",
            "cancel_requested": "cancel",
            "cancelled": "cancel",
        }[target_state]
    except KeyError as exc:
        raise SpeechReconciliationRepositoryError(
            "speech_reconciliation_mutation_operation_invalid",
            status_code=422,
        ) from exc


def _require_mutation_digest(value: str, reason_code: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise SpeechReconciliationRepositoryError(reason_code, status_code=422)


def _mutation_receipt(
    session: Session,
    *,
    tenant_id: str,
    owner_subject: str,
    job_id: str,
    operation: str,
    idempotency_key_digest: str,
) -> SpeechReconciliationMutationDB | None:
    return session.exec(
        select(SpeechReconciliationMutationDB)
        .where(
            SpeechReconciliationMutationDB.tenant_id == tenant_id,
            SpeechReconciliationMutationDB.owner_subject == owner_subject,
            SpeechReconciliationMutationDB.job_id == job_id,
            SpeechReconciliationMutationDB.operation == operation,
            SpeechReconciliationMutationDB.idempotency_key_digest == idempotency_key_digest,
        )
        .with_for_update()
    ).first()


def _new_mutation_receipt(
    *,
    row: SpeechReconciliationJobDB,
    operation: str,
    idempotency_key_digest: str,
    request_digest: str,
    now_ms: int,
    state_changed: bool,
    affected_attempt_id: str | None = None,
    affected_fencing_epoch: int | None = None,
) -> SpeechReconciliationMutationDB:
    snapshot = asdict(_job(row))
    return SpeechReconciliationMutationDB(
        tenant_id=row.tenant_id,
        owner_subject=row.owner_subject,
        job_id=row.id,
        operation=operation,
        idempotency_key_digest=idempotency_key_digest,
        request_digest=request_digest,
        result_job_version=row.version,
        result_snapshot=snapshot,
        affected_attempt_id=affected_attempt_id,
        affected_fencing_epoch=affected_fencing_epoch,
        state_changed=state_changed,
        created_at_ms=now_ms,
    )


def _job_from_snapshot(raw: Mapping[str, Any]) -> SpeechReconciliationJobRecord:
    expected = set(SpeechReconciliationJobRecord.__dataclass_fields__)
    if set(raw) != expected:
        raise SpeechReconciliationRepositoryError(
            "speech_reconciliation_mutation_receipt_corrupt",
            status_code=500,
        )
    try:
        return SpeechReconciliationJobRecord(**dict(raw))
    except (TypeError, ValueError) as exc:
        raise SpeechReconciliationRepositoryError(
            "speech_reconciliation_mutation_receipt_corrupt",
            status_code=500,
        ) from exc


def _result_from_receipt(
    receipt: SpeechReconciliationMutationDB,
    *,
    applied: bool,
) -> SpeechReconciliationMutationResult:
    return SpeechReconciliationMutationResult(
        job=_job_from_snapshot(receipt.result_snapshot),
        applied=applied,
        affected_attempt_id=receipt.affected_attempt_id,
        affected_fencing_epoch=receipt.affected_fencing_epoch,
    )


def _replay_mutation(
    receipt: SpeechReconciliationMutationDB,
    *,
    request_digest: str,
) -> SpeechReconciliationMutationResult:
    if receipt.request_digest != request_digest:
        raise SpeechReconciliationRepositoryError("speech_reconciliation_idempotency_conflict")
    return _result_from_receipt(receipt, applied=False)


def _mutation_winner_or_raise(
    session: Session,
    *,
    tenant_id: str,
    owner_subject: str,
    job_id: str,
    operation: str,
    idempotency_key_digest: str,
    request_digest: str,
    cause: IntegrityError,
) -> SpeechReconciliationMutationResult:
    winner = _mutation_receipt(
        session,
        tenant_id=tenant_id,
        owner_subject=owner_subject,
        job_id=job_id,
        operation=operation,
        idempotency_key_digest=idempotency_key_digest,
    )
    if winner is None:
        raise SpeechReconciliationRepositoryError("speech_reconciliation_write_conflict") from cause
    return _replay_mutation(winner, request_digest=request_digest)


def _attempt(row: SpeechReconciliationAttemptDB) -> SpeechReconciliationAttemptRecord:
    return SpeechReconciliationAttemptRecord(
        **{field: getattr(row, field) for field in SpeechReconciliationAttemptRecord.__dataclass_fields__}
    )


def _active_job_contract(
    job: SpeechReconciliationJobDB,
    attempt: SpeechReconciliationAttemptDB,
) -> SpeechReconciliationJob:
    return SpeechReconciliationJob.from_mapping(
        {
            "contract_version": CONTRACT_VERSION,
            "job_id": job.id,
            "attempt_id": attempt.id,
            "fencing_token_digest": attempt.fencing_token_digest,
            "fencing_epoch": attempt.fencing_epoch,
            "consent_id": job.consent_id,
            "consent_version": job.consent_version,
            "revocation_epoch": job.revocation_epoch,
            "input_manifest_digest": job.input_manifest_digest,
            "input_lineage_digest": job.input_lineage_digest,
            "input_artifact_ref": job.input_artifact_ref,
            "policy_digest": job.policy_digest,
            "research_policy_ref": job.research_policy_ref,
            "source_duration_ms": job.source_duration_ms,
            "max_compute_factor": job.max_compute_factor,
            "ledger_sequence": job.ledger_sequence,
            "key_epoch": job.key_epoch,
            "deadline_at_ms": job.deadline_at_ms,
            "stage": job.stage,
        }
    )


def _ledger(row: SpeechReconciliationBudgetLedgerDB) -> SpeechReconciliationBudgetLedger:
    return SpeechReconciliationBudgetLedger.from_mapping(
        {
            "contract_version": CONTRACT_VERSION,
            "job_id": row.job_id,
            "attempt_id": row.attempt_id,
            "fencing_epoch": row.fencing_epoch,
            "sequence": row.sequence,
            "stage": row.stage,
            "source_duration_ms": row.source_duration_ms,
            "compute_factor": row.compute_factor,
            "allocated": row.allocated,
            "reserved": row.reserved,
            "consumed": row.consumed,
            "remaining": row.remaining,
        }
    )


def _now(value: int | None) -> int:
    return time.time_ns() // 1_000_000 if value is None else int(value)


def _validate_budget_plan(value: Mapping[str, object], *, factor: int) -> dict[str, object]:
    if set(value) != {"compute_factor", "compute_equivalent_ms", "allocated", "stages"}:
        raise SpeechReconciliationRepositoryError(
            "speech_reconciliation_budget_plan_invalid",
            status_code=422,
        )
    if value.get("compute_factor") != factor:
        raise SpeechReconciliationRepositoryError(
            "speech_reconciliation_budget_plan_factor_mismatch",
            status_code=422,
        )
    compute_equivalent_ms = value.get("compute_equivalent_ms")
    if (
        isinstance(compute_equivalent_ms, bool)
        or not isinstance(compute_equivalent_ms, int)
        or not 1 <= compute_equivalent_ms <= 2**63 - 1
    ):
        raise SpeechReconciliationRepositoryError(
            "speech_reconciliation_budget_plan_invalid",
            status_code=422,
        )
    try:
        allocated = SpeechResourceVector.from_mapping(value.get("allocated"), "budget_plan.allocated")
    except Exception as exc:
        raise SpeechReconciliationRepositoryError(
            "speech_reconciliation_budget_plan_invalid",
            status_code=422,
        ) from exc
    raw_stages = value.get("stages")
    if not isinstance(raw_stages, Mapping) or not raw_stages or any(stage not in STAGES for stage in raw_stages):
        raise SpeechReconciliationRepositoryError(
            "speech_reconciliation_budget_plan_invalid",
            status_code=422,
        )
    stages: dict[str, dict[str, int]] = {}
    summed = SpeechResourceVector()
    try:
        for stage, raw in sorted(raw_stages.items()):
            vector = SpeechResourceVector.from_mapping(raw, f"budget_plan.stages.{stage}")
            stages[str(stage)] = vector.to_dict()
            summed = summed.add(vector)
    except Exception as exc:
        raise SpeechReconciliationRepositoryError(
            "speech_reconciliation_budget_plan_invalid",
            status_code=422,
        ) from exc
    if summed != allocated:
        raise SpeechReconciliationRepositoryError(
            "speech_reconciliation_budget_plan_arithmetic_invalid",
            status_code=422,
        )
    return {
        "compute_factor": factor,
        "compute_equivalent_ms": compute_equivalent_ms,
        "allocated": allocated.to_dict(),
        "stages": stages,
    }


__all__ = [
    "SpeechReconciliationAttemptRecord",
    "SpeechReconciliationCollectibleAttempt",
    "SpeechReconciliationJobCreate",
    "SpeechReconciliationJobRecord",
    "SpeechReconciliationMutationResult",
    "SpeechReconciliationRepository",
    "SpeechReconciliationRepositoryError",
    "SqlSpeechReconciliationBudgetRepository",
]
