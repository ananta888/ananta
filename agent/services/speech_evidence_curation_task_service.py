"""Idempotent Hub-owned task flow from admitted evidence to curation."""

from __future__ import annotations

import hashlib
import time
from typing import Callable, Mapping, Protocol

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from agent.database import engine
from agent.db_models.speech_evidence import (
    SpeechCurationTaskDB,
    SpeechEvidenceAdmissionDB,
    SpeechEvidenceConsentDB,
    SpeechEvidenceDB,
)
from agent.repositories.speech_evidence_lineage import (
    SpeechEvidenceLineageRepository,
    SpeechLineageEdge,
    SpeechLineageNode,
    get_speech_evidence_lineage_repository,
)
from agent.services.speech_evidence_consent_service import (
    SpeechEvidenceConsentService,
    get_speech_evidence_consent_service,
)
from agent.services.voice_governance_domain import VoicePrincipal
from ananta_contracts.speech_evidence_governance import (
    SpeechCurationWorkerResult,
    SpeechCurationWorkerTask,
)


class SpeechCurationTaskError(RuntimeError):
    def __init__(self, reason_code: str, *, status_code: int = 409) -> None:
        self.reason_code = reason_code
        self.status_code = status_code
        super().__init__(reason_code)


class SpeechCurationQueuePort(Protocol):
    def ingest_task(self, **kwargs: object) -> None: ...


class SpeechCurationResultPort(Protocol):
    """Idempotently publish a content-addressed artifact.

    Implementations must treat an identical ``artifact_ref``/digest replay as
    success.  The Hub holds its task/consent write fence during this bounded
    call, making revoke-vs-publish decisions linearizable.
    """

    def publish(self, result: SpeechCurationWorkerResult) -> bool: ...


class UnavailableSpeechCurationResultPort:
    def publish(self, _result: SpeechCurationWorkerResult) -> bool:
        raise SpeechCurationTaskError("speech_curation_result_port_unavailable", status_code=503)


class SpeechEvidenceCurationTaskService:
    MAX_DEADLINE_MS = 5 * 60 * 1000
    LIMITS = {"max_input_refs": 64, "max_output_bytes": 8 * 1024 * 1024, "cpu_ms": 120_000}

    def __init__(
        self,
        *,
        queue: SpeechCurationQueuePort | None = None,
        result_port: SpeechCurationResultPort | None = None,
        consent: SpeechEvidenceConsentService | None = None,
        clock_ms: Callable[[], int] | None = None,
        lineage: SpeechEvidenceLineageRepository | None = None,
    ) -> None:
        self._queue = queue or _default_queue()
        self._result_port = result_port or UnavailableSpeechCurationResultPort()
        self._consent = consent or get_speech_evidence_consent_service()
        self._clock_ms = clock_ms or (lambda: time.time_ns() // 1_000_000)
        self._lineage = lineage or get_speech_evidence_lineage_repository()

    def create(
        self,
        principal: VoicePrincipal,
        *,
        admission_digest: str,
        authority: str = "hub",
    ) -> tuple[SpeechCurationWorkerTask, bool]:
        if authority != "hub":
            raise SpeechCurationTaskError("speech_curation_hub_authority_required", status_code=403)
        if not _digest(admission_digest):
            raise SpeechCurationTaskError("speech_curation_admission_digest_invalid", status_code=422)
        existing = self._get_by_admission(principal, admission_digest)
        if existing is not None:
            task = _task_from_row(existing)
            if existing.state == "pending_queue":
                self._enqueue(task)
                self._mark_queued(task.task_id, task.fencing_token)
            return task, False
        # Dataset/training consent is deliberately not checked here: curation
        # remains a separate, reversible stage.
        now = self._clock_ms()
        try:
            with Session(engine) as session:
                admission = session.exec(
                    select(SpeechEvidenceAdmissionDB).where(
                        SpeechEvidenceAdmissionDB.tenant_id == principal.tenant_id,
                        SpeechEvidenceAdmissionDB.owner_subject == principal.subject,
                        SpeechEvidenceAdmissionDB.admission_digest == admission_digest,
                        SpeechEvidenceAdmissionDB.decision == "admitted",
                    )
                ).first()
                if admission is None:
                    raise SpeechCurationTaskError("speech_curation_admission_not_found", status_code=404)
                evidence = session.exec(
                    select(SpeechEvidenceDB).where(
                        SpeechEvidenceDB.id == admission.evidence_id,
                        SpeechEvidenceDB.tenant_id == principal.tenant_id,
                        SpeechEvidenceDB.owner_subject == principal.subject,
                        SpeechEvidenceDB.state == "admitted",
                        SpeechEvidenceDB.admission_digest == admission_digest,
                    )
                ).first()
                if evidence is None:
                    raise SpeechCurationTaskError("speech_curation_evidence_not_admitted")
                # This compare-and-no-op update is also the SQLite writer
                # fence; on row-locking databases it complements FOR UPDATE.
                consent_fence = session.exec(
                    update(SpeechEvidenceConsentDB)
                    .where(
                        SpeechEvidenceConsentDB.id == evidence.consent_id,
                        SpeechEvidenceConsentDB.tenant_id == principal.tenant_id,
                        SpeechEvidenceConsentDB.owner_subject == principal.subject,
                        SpeechEvidenceConsentDB.state == "active",
                        SpeechEvidenceConsentDB.consent_version == evidence.consent_version,
                        SpeechEvidenceConsentDB.revocation_epoch == evidence.revocation_epoch,
                        SpeechEvidenceConsentDB.expires_at_ms > now,
                    )
                    .values(consent_version=SpeechEvidenceConsentDB.consent_version)
                )
                if consent_fence.rowcount != 1:
                    session.rollback()
                    raise SpeechCurationTaskError("speech_curation_consent_stale", status_code=403)
                task_id = f"speech-curation-{hashlib.sha256(admission_digest.encode()).hexdigest()[:32]}"
                task = SpeechCurationWorkerTask(
                    task_id=task_id,
                    parent_task_id=task_id,
                    admission_digest=admission_digest,
                    evidence_refs=(f"speech-evidence-ref:{evidence.id}",),
                    consent_id=evidence.consent_id,
                    consent_version=evidence.consent_version,
                    revocation_epoch=evidence.revocation_epoch,
                    deadline_epoch_ms=now + self.MAX_DEADLINE_MS,
                    limits=dict(self.LIMITS),
                    artifact_publish_ref=f"artifact://speech-curation/{task_id}/result",
                    fencing_token=1,
                )
                row = SpeechCurationTaskDB(
                    id=task.task_id,
                    tenant_id=principal.tenant_id,
                    owner_subject=principal.subject,
                    parent_task_id=task.parent_task_id,
                    admission_digest=task.admission_digest,
                    evidence_refs=list(task.evidence_refs),
                    consent_id=task.consent_id,
                    consent_version=task.consent_version,
                    revocation_epoch=task.revocation_epoch,
                    fencing_token=task.fencing_token,
                    task_binding=task.to_dict(),
                    state="pending_queue",
                    deadline_epoch_ms=task.deadline_epoch_ms,
                    created_at_ms=now,
                    updated_at_ms=now,
                )
                session.add(row)
                session.commit()
        except IntegrityError:
            concurrent = self._get_by_admission(principal, admission_digest)
            if concurrent is None:
                raise
            return _task_from_row(concurrent), False
        self._enqueue(task)
        self._mark_queued(task.task_id, task.fencing_token)
        return task, True

    def authorize_result(
        self,
        principal: VoicePrincipal,
        raw: object,
        *,
        authority: str = "hub",
        expected_executor_id: str | None = None,
    ) -> SpeechCurationWorkerResult:
        if authority != "hub":
            raise SpeechCurationTaskError("speech_curation_hub_authority_required", status_code=403)
        result = SpeechCurationWorkerResult.from_mapping(raw)
        now = self._clock_ms()
        event_digest: str | None = None
        with Session(engine) as session:
            row = session.exec(
                select(SpeechCurationTaskDB)
                .where(
                    SpeechCurationTaskDB.id == result.task_id,
                    SpeechCurationTaskDB.tenant_id == principal.tenant_id,
                    SpeechCurationTaskDB.owner_subject == principal.subject,
                )
                .with_for_update()
            ).first()
            if row is None:
                raise SpeechCurationTaskError("speech_curation_task_not_found", status_code=404)
            task = _task_from_row(row)
            if row.state == "completed":
                if expected_executor_id is not None and row.executor_id != expected_executor_id:
                    raise SpeechCurationTaskError("speech_curation_executor_mismatch", status_code=403)
                if (
                    result.admission_digest == task.admission_digest
                    and result.artifact_ref == task.artifact_publish_ref
                    and result.consent_version == task.consent_version
                    and result.revocation_epoch == task.revocation_epoch
                    and result.fencing_token == task.fencing_token
                    and result.artifact_ref == row.result_artifact_ref
                    and result.artifact_digest == row.result_artifact_digest
                ):
                    return result
                raise SpeechCurationTaskError("speech_curation_result_replay_mismatch")
            if row.state not in {"queued", "running"}:
                raise SpeechCurationTaskError("speech_curation_task_fenced")
            if expected_executor_id is not None and row.executor_id != expected_executor_id:
                raise SpeechCurationTaskError("speech_curation_executor_mismatch", status_code=403)
            if (
                result.admission_digest != task.admission_digest
                or result.artifact_ref != task.artifact_publish_ref
                or result.consent_version != task.consent_version
                or result.revocation_epoch != task.revocation_epoch
                or result.fencing_token != task.fencing_token
            ):
                raise SpeechCurationTaskError("speech_curation_result_binding_mismatch")
            if result.completed_at_ms > task.deadline_epoch_ms or now > task.deadline_epoch_ms:
                raise SpeechCurationTaskError("speech_curation_result_late")
            current = session.exec(
                select(SpeechEvidenceConsentDB)
                .where(
                    SpeechEvidenceConsentDB.id == task.consent_id,
                    SpeechEvidenceConsentDB.tenant_id == principal.tenant_id,
                    SpeechEvidenceConsentDB.owner_subject == principal.subject,
                )
                .with_for_update()
            ).first()
            if current is None or (
                current.state != "active"
                or current.expires_at_ms <= now
                or current.consent_version != task.consent_version
                or current.revocation_epoch != task.revocation_epoch
            ):
                row.state = "cancelled"
                row.fencing_token += 1
                row.updated_at_ms = now
                session.add(row)
                session.commit()
                raise SpeechCurationTaskError("speech_curation_consent_stale", status_code=403)
            admission = session.exec(
                select(SpeechEvidenceAdmissionDB).where(
                    SpeechEvidenceAdmissionDB.admission_digest == result.admission_digest,
                    SpeechEvidenceAdmissionDB.tenant_id == principal.tenant_id,
                    SpeechEvidenceAdmissionDB.owner_subject == principal.subject,
                )
            ).first()
            if admission is None:
                raise SpeechCurationTaskError("speech_curation_admission_not_found", status_code=404)
            claimed = session.exec(
                update(SpeechCurationTaskDB)
                .where(
                    SpeechCurationTaskDB.id == task.task_id,
                    SpeechCurationTaskDB.state.in_(["queued", "running"]),
                    SpeechCurationTaskDB.fencing_token == task.fencing_token,
                )
                .values(state="publishing", updated_at_ms=now)
            )
            if claimed.rowcount != 1:
                session.rollback()
                raise SpeechCurationTaskError("speech_curation_result_lost_fence")
            # The write above takes the database writer fence before the
            # artifact becomes visible.  A concurrent revoke therefore wins
            # either before this claim (publish rejected) or after this
            # transaction (the newly staged lineage is included in impact).
            try:
                published = self._result_port.publish(result)
            except SpeechCurationTaskError:
                session.rollback()
                raise
            except Exception as exc:
                session.rollback()
                raise SpeechCurationTaskError("speech_curation_result_publish_failed", status_code=503) from exc
            if published is not True:
                session.rollback()
                raise SpeechCurationTaskError("speech_curation_result_publish_rejected", status_code=503)
            session.exec(
                update(SpeechCurationTaskDB)
                .where(
                    SpeechCurationTaskDB.id == task.task_id,
                    SpeechCurationTaskDB.state == "publishing",
                    SpeechCurationTaskDB.fencing_token == task.fencing_token,
                )
                .values(
                    state="completed",
                    result_artifact_ref=result.artifact_ref,
                    result_artifact_digest=result.artifact_digest,
                    updated_at_ms=now,
                )
            )
            event_digest = self._lineage.stage(
                session,
                tenant_id=principal.tenant_id,
                owner_subject=principal.subject,
                nodes=(
                    SpeechLineageNode(
                        "evidence",
                        admission.evidence_digest,
                        consent_id=task.consent_id,
                        revocation_epoch=task.revocation_epoch,
                    ),
                    SpeechLineageNode(
                        "reconciliation",
                        result.artifact_digest,
                        consent_id=task.consent_id,
                        revocation_epoch=task.revocation_epoch,
                    ),
                ),
                edges=(
                    SpeechLineageEdge(
                        "evidence",
                        admission.evidence_digest,
                        "reconciliation",
                        result.artifact_digest,
                        "curated_into",
                    ),
                ),
                now_ms=now,
            )
            session.commit()
        if event_digest is not None:
            self._lineage.process_outbox(
                event_digest=event_digest,
                tenant_id=principal.tenant_id,
                owner_subject=principal.subject,
            )
        return result

    def claim_execution(
        self,
        principal: VoicePrincipal,
        task_id: str,
        *,
        executor_id: str,
        executor_url: str,
        authority: str = "hub",
    ) -> SpeechCurationWorkerTask:
        """Bind one delegated task to exactly one authenticated Worker."""

        if authority != "hub":
            raise SpeechCurationTaskError("speech_curation_hub_authority_required", status_code=403)
        if not _safe_executor(executor_id) or not _safe_executor(executor_url, maximum=512):
            raise SpeechCurationTaskError("speech_curation_executor_invalid", status_code=422)
        now = self._clock_ms()
        with Session(engine) as session:
            row = session.exec(
                select(SpeechCurationTaskDB)
                .where(
                    SpeechCurationTaskDB.id == task_id,
                    SpeechCurationTaskDB.tenant_id == principal.tenant_id,
                    SpeechCurationTaskDB.owner_subject == principal.subject,
                )
                .with_for_update()
            ).first()
            if row is None:
                raise SpeechCurationTaskError("speech_curation_task_not_found", status_code=404)
            if row.state == "running" and row.executor_id == executor_id and row.executor_url == executor_url:
                return _task_from_row(row)
            if row.state != "queued" or row.executor_id is not None:
                raise SpeechCurationTaskError("speech_curation_task_already_claimed", status_code=409)
            claimed = session.exec(
                update(SpeechCurationTaskDB)
                .where(
                    SpeechCurationTaskDB.id == task_id,
                    SpeechCurationTaskDB.state == "queued",
                    SpeechCurationTaskDB.executor_id.is_(None),
                    SpeechCurationTaskDB.fencing_token == row.fencing_token,
                    SpeechCurationTaskDB.deadline_epoch_ms > now,
                )
                .values(
                    state="running",
                    executor_id=executor_id,
                    executor_url=executor_url,
                    updated_at_ms=now,
                )
            )
            if claimed.rowcount != 1:
                session.rollback()
                raise SpeechCurationTaskError("speech_curation_task_claim_conflict", status_code=409)
            session.commit()
            row.state = "running"
            row.executor_id = executor_id
            row.executor_url = executor_url
            return _task_from_row(row)

    def fence(self, principal: VoicePrincipal, task_id: str, *, reason_code: str) -> bool:
        # reason_code is intentionally not persisted in the worker contract.
        if not reason_code.startswith("speech_"):
            raise SpeechCurationTaskError("speech_curation_reason_invalid", status_code=422)
        with Session(engine) as session:
            result = session.exec(
                update(SpeechCurationTaskDB)
                .where(
                    SpeechCurationTaskDB.id == task_id,
                    SpeechCurationTaskDB.tenant_id == principal.tenant_id,
                    SpeechCurationTaskDB.owner_subject == principal.subject,
                    SpeechCurationTaskDB.state.in_(["pending_queue", "queued", "running", "publishing"]),
                )
                .values(
                    state="cancelled",
                    fencing_token=SpeechCurationTaskDB.fencing_token + 1,
                    updated_at_ms=self._clock_ms(),
                )
            )
            session.commit()
            return result.rowcount == 1

    def _enqueue(self, task: SpeechCurationWorkerTask) -> None:
        self._queue.ingest_task(
            task_id=task.task_id,
            status="assigned",
            title="Hub-admitted speech evidence curation",
            description="Curate opaque speech evidence under bounded Hub policy.",
            priority="medium",
            created_by="hub",
            source="speech_evidence_curation",
            tags=["speech_evidence", "curation"],
            event_type="speech_evidence_curation_delegated",
            event_details={"admission_digest": task.admission_digest},
            extra_fields={
                "task_kind": "speech_evidence_curation",
                "required_capabilities": ["speech_evidence_curation"],
                "worker_execution_context": {"speech_evidence_curation": task.to_dict()},
            },
        )

    def _mark_queued(self, task_id: str, fencing_token: int) -> None:
        with Session(engine) as session:
            session.exec(
                update(SpeechCurationTaskDB)
                .where(
                    SpeechCurationTaskDB.id == task_id,
                    SpeechCurationTaskDB.state == "pending_queue",
                    SpeechCurationTaskDB.fencing_token == fencing_token,
                )
                .values(state="queued", updated_at_ms=self._clock_ms())
            )
            session.commit()

    @staticmethod
    def _get_by_admission(principal: VoicePrincipal, admission_digest: str) -> SpeechCurationTaskDB | None:
        with Session(engine) as session:
            return session.exec(
                select(SpeechCurationTaskDB).where(
                    SpeechCurationTaskDB.tenant_id == principal.tenant_id,
                    SpeechCurationTaskDB.owner_subject == principal.subject,
                    SpeechCurationTaskDB.admission_digest == admission_digest,
                )
            ).first()


def _task_from_row(row: SpeechCurationTaskDB) -> SpeechCurationWorkerTask:
    binding: Mapping[str, object] = dict(row.task_binding or {})
    return SpeechCurationWorkerTask(
        task_id=str(binding["task_id"]),
        parent_task_id=str(binding["parent_task_id"]),
        admission_digest=str(binding["admission_digest"]),
        evidence_refs=tuple(str(item) for item in binding["evidence_refs"]),
        consent_id=str(binding["consent_id"]),
        consent_version=int(binding["consent_version"]),
        revocation_epoch=int(binding["revocation_epoch"]),
        deadline_epoch_ms=int(binding["deadline_epoch_ms"]),
        limits={str(key): int(value) for key, value in dict(binding["limits"]).items()},
        artifact_publish_ref=str(binding["artifact_publish_ref"]),
        fencing_token=int(row.fencing_token),
    )


def _default_queue():
    from agent.services.task_queue_service import get_task_queue_service

    return get_task_queue_service()


def _digest(value: str) -> bool:
    return len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def _safe_executor(value: str, *, maximum: int = 256) -> bool:
    return isinstance(value, str) and 1 <= len(value) <= maximum and not any(character.isspace() for character in value)


_service: SpeechEvidenceCurationTaskService | None = None


def get_speech_evidence_curation_task_service() -> SpeechEvidenceCurationTaskService:
    global _service
    if _service is None:
        _service = SpeechEvidenceCurationTaskService()
    return _service


__all__ = [
    "SpeechCurationQueuePort",
    "SpeechCurationResultPort",
    "SpeechCurationTaskError",
    "SpeechEvidenceCurationTaskService",
    "UnavailableSpeechCurationResultPort",
    "get_speech_evidence_curation_task_service",
]
