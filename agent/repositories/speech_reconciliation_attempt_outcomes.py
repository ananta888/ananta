"""Checkpoint, quality-decision and final-result admission of fenced attempts."""

from __future__ import annotations

from typing import Mapping

from sqlmodel import Session, select

from agent.database import engine
from agent.db_models.speech_reconciliation import (
    SpeechReconciliationArtifactDB,
    SpeechReconciliationCheckpointDB,
)
from agent.models.speech_reconciliation_state_machine import SpeechReconciliationStateMachine
from agent.repositories.speech_evidence_lineage import (
    SpeechEvidenceLineageRepository,
    SpeechLineageEdge,
    SpeechLineageNode,
)
from agent.repositories.speech_reconciliation_audit_stager import SpeechReconciliationAuditStager
from agent.repositories.speech_reconciliation_locking import (
    locked_attempt,
    locked_job,
    require_current_fence,
)
from agent.repositories.speech_reconciliation_records import (
    SpeechReconciliationAttemptRecord,
    SpeechReconciliationJobRecord,
    SpeechReconciliationRepositoryError,
    attempt_record,
    job_record,
    resolve_now_ms,
)
from ananta_contracts.speech_reconciliation import (
    SpeechReconciliationCheckpoint,
    SpeechReconciliationJob,
    SpeechReconciliationResult,
    assert_result_matches_job,
)


class SpeechReconciliationAttemptOutcomes:
    """Admits worker-reported progress only under the current attempt fence."""

    def __init__(
        self,
        *,
        lineage: SpeechEvidenceLineageRepository,
        states: SpeechReconciliationStateMachine,
        audit: SpeechReconciliationAuditStager,
    ) -> None:
        self._lineage = lineage
        self._states = states
        self._audit = audit


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
        now = resolve_now_ms(now_ms)
        with Session(engine) as session:
            job = locked_job(session, tenant_id, owner_subject, job_contract.job_id)
            attempt = locked_attempt(session, job.id, checkpoint.attempt_id)
            require_current_fence(job, attempt, checkpoint.fencing_epoch, checkpoint.fencing_token_digest, now)
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
                return attempt_record(attempt)
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
            self._audit.enqueue(
                session,
                self._audit.prepare(
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
            return attempt_record(attempt)

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
        now = resolve_now_ms(now_ms)
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
            job = locked_job(session, tenant_id, owner_subject, job_contract.job_id)
            attempt = locked_attempt(session, job.id, job_contract.attempt_id)
            history = [dict(value) for value in (job.quality_history or []) if isinstance(value, Mapping)]
            previous = next(
                (value for value in history if value.get("attempt_id") == job_contract.attempt_id),
                None,
            )
            if previous is not None:
                if previous != observation:
                    raise SpeechReconciliationRepositoryError("speech_reconciliation_quality_decision_conflict")
                return job_record(job)
            require_current_fence(
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
            self._audit.enqueue(
                session,
                self._audit.prepare(
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
            return job_record(job)

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
        now = resolve_now_ms(now_ms)
        with Session(engine) as session:
            job = locked_job(session, tenant_id, owner_subject, job_contract.job_id)
            attempt = locked_attempt(session, job.id, result.attempt_id)
            require_current_fence(job, attempt, result.fencing_epoch, result.fencing_token_digest, now)
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
            self._audit.enqueue(
                session,
                self._audit.prepare(
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
        return job_record(job)

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


__all__ = ["SpeechReconciliationAttemptOutcomes"]
