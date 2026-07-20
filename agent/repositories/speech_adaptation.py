"""SQL adapters for the Hub-owned speech-adaptation control plane."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
import time
from pathlib import Path
from typing import BinaryIO

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, delete, select, update

from agent.database import engine
from agent.db_models.ml_intern_training import MlInternSpeechAdapterDB
from agent.db_models.speech_adaptation import (
    SpeechAdaptationArtifactDB,
    SpeechAdaptationCapacityLeaseDB,
    SpeechAdaptationJobDB,
)
from agent.db_models.speech_evidence import SpeechEvidenceConsentDB
from agent.repositories.semantic_media_audit_outbox import SqlSemanticMediaAuditOutbox
from agent.services.semantic_media_audit_service import (
    SemanticMediaAuditEvent,
    SemanticMediaAuditPort,
)
from agent.services.speech_adaptation_job_service import (
    SpeechAdaptationDecisionConflict,
    SpeechAdmissionDecision,
    SpeechCapacityLease,
    SpeechPrincipal,
    restore_speech_adaptation_job,
)
from ananta_contracts.speech_adaptation import SpeechAdaptationResult

_WRITE_LOCK = threading.RLock()
_TERMINAL = frozenset({"completed", "dataset_only", "cancelled", "failed", "denied"})
_ACTIVE = frozenset({"queued", "dispatching", "submitted", "running", "cancel_requested"})


class SqlSpeechAdaptationDecisionStore:
    """Tenant-scoped, CAS-protected admission and worker state."""

    def __init__(self, *, audit: SemanticMediaAuditPort | None = None) -> None:
        self._audit = audit

    @property
    def transactional_audit(self) -> bool:
        return self._audit is not None

    def _audit_event(
        self,
        *,
        tenant_id: str,
        job_id: str,
        version: int,
        status: str,
        reason_code: str,
        contract_payload: dict | None,
    ) -> SemanticMediaAuditEvent | None:
        if self._audit is None:
            return None
        epoch = 1
        lease_ref = None
        if contract_payload:
            job = restore_speech_adaptation_job(contract_payload)
            epoch = max(1, job.fencing.epoch)
            lease_ref = job.fencing.lease_id
        return self._audit.prepare_transition(
            idempotency_key=f"speech-training:{job_id}:{version}:{status}:{reason_code}",
            tenant_id=tenant_id,
            scope=f"speech-job:{job_id}",
            event_type="speech_training",
            transition=status,
            reason_code=reason_code,
            epoch=epoch,
            lease_ref=lease_ref,
            job_ref=job_id,
        )

    def by_idempotency(
        self,
        principal: SpeechPrincipal,
        idempotency_digest: str,
    ) -> SpeechAdmissionDecision | None:
        with Session(engine) as session:
            row = session.exec(
                self._scope(principal).where(SpeechAdaptationJobDB.idempotency_digest == idempotency_digest)
            ).first()
            return _decision(row) if row is not None else None

    def create(
        self,
        principal: SpeechPrincipal,
        *,
        idempotency_digest: str,
        decision: SpeechAdmissionDecision,
    ) -> tuple[SpeechAdmissionDecision, bool]:
        row = SpeechAdaptationJobDB(
            id=decision.job_id,
            tenant_id=principal.tenant_id,
            owner_subject=principal.subject,
            task_id=decision.task_id,
            idempotency_digest=idempotency_digest,
            request_digest=decision.request_digest,
            status=decision.status,
            reason_code=decision.reason_code,
            admission_request_payload=dict(decision.admission_request or {}),
            contract_payload=decision.job.to_dict() if decision.job is not None else {},
            terminal_at_ms=(time.time_ns() // 1_000_000 if decision.status in _TERMINAL else None),
        )
        with _WRITE_LOCK:
            with Session(engine) as session:
                existing = session.exec(
                    self._scope(principal).where(SpeechAdaptationJobDB.idempotency_digest == idempotency_digest)
                ).first()
                if existing is not None:
                    return self._replay(existing, decision), True
                session.add(row)
                audit_event = self._audit_event(
                    tenant_id=principal.tenant_id,
                    job_id=row.id,
                    version=row.version,
                    status=row.status,
                    reason_code=row.reason_code,
                    contract_payload=dict(row.contract_payload or {}),
                )
                try:
                    if audit_event is not None:
                        SqlSemanticMediaAuditOutbox.enqueue_in_session(session, audit_event)
                    session.commit()
                    session.refresh(row)
                    return _decision(row), False
                except IntegrityError:
                    session.rollback()
                    existing = session.exec(
                        self._scope(principal).where(SpeechAdaptationJobDB.idempotency_digest == idempotency_digest)
                    ).first()
                    if existing is None:
                        raise
                    return self._replay(existing, decision), True

    def get(self, principal: SpeechPrincipal, job_id: str) -> SpeechAdmissionDecision | None:
        with Session(engine) as session:
            row = session.exec(self._scope(principal).where(SpeechAdaptationJobDB.id == job_id)).first()
            return _decision(row) if row is not None else None

    def waiting_admission(
        self,
        principal: SpeechPrincipal,
        job_id: str,
    ) -> tuple[str, dict] | None:
        with Session(engine) as session:
            row = session.exec(
                self._scope(principal).where(
                    SpeechAdaptationJobDB.id == job_id,
                    SpeechAdaptationJobDB.status == "queued",
                )
            ).first()
            if row is None or dict(row.contract_payload or {}) or not dict(row.admission_request_payload or {}):
                return None
            return row.idempotency_digest, dict(row.admission_request_payload)

    def get_row(self, job_id: str) -> SpeechAdaptationJobDB | None:
        with Session(engine) as session:
            row = session.get(SpeechAdaptationJobDB, job_id)
            if row is not None:
                session.expunge(row)
            return row

    def replace(
        self,
        principal: SpeechPrincipal,
        decision: SpeechAdmissionDecision,
        *,
        expected_statuses: frozenset[str],
        result: SpeechAdaptationResult | None = None,
    ) -> SpeechAdmissionDecision:
        with _WRITE_LOCK:
            current = self.get_row(decision.job_id)
            if (
                current is None
                or current.tenant_id != principal.tenant_id
                or current.owner_subject != principal.subject
            ):
                raise SpeechAdaptationDecisionConflict("speech_job_not_found")
            expected_contract = decision.job.to_dict() if decision.job is not None else {}
            expected_request = dict(decision.admission_request or {})
            expected_result = result.to_dict() if result is not None else None
            if (
                current.status == decision.status
                and current.reason_code == decision.reason_code
                and dict(current.contract_payload or {}) == expected_contract
                and dict(current.admission_request_payload or {}) == expected_request
                and (expected_result is None or dict(current.result_payload or {}) == expected_result)
            ):
                return _decision(current)
            if current.status not in expected_statuses:
                raise SpeechAdaptationDecisionConflict("speech_job_state_conflict")
            values: dict[str, object] = {
                "status": decision.status,
                "reason_code": decision.reason_code,
                "task_id": decision.task_id,
                "admission_request_payload": expected_request,
                "contract_payload": expected_contract,
                "version": current.version + 1,
                "updated_at_ms": time.time_ns() // 1_000_000,
            }
            if decision.status in _TERMINAL:
                values["terminal_at_ms"] = time.time_ns() // 1_000_000
            if result is not None:
                values["result_payload"] = expected_result
            with Session(engine) as session:
                changed = session.exec(
                    update(SpeechAdaptationJobDB)
                    .where(
                        SpeechAdaptationJobDB.id == current.id,
                        SpeechAdaptationJobDB.version == current.version,
                        SpeechAdaptationJobDB.status == current.status,
                    )
                    .values(**values)
                )
                if changed.rowcount != 1:
                    session.rollback()
                    raise SpeechAdaptationDecisionConflict("speech_job_state_conflict")
                audit_event = self._audit_event(
                    tenant_id=principal.tenant_id,
                    job_id=current.id,
                    version=current.version + 1,
                    status=decision.status,
                    reason_code=decision.reason_code,
                    contract_payload=expected_contract,
                )
                if audit_event is not None:
                    SqlSemanticMediaAuditOutbox.enqueue_in_session(session, audit_event)
                session.commit()
            saved = self.get(principal, decision.job_id)
            if saved is None:
                raise SpeechAdaptationDecisionConflict("speech_job_not_found")
            return saved

    def list_dispatchable(self, *, now_ms: int, limit: int) -> tuple[SpeechAdaptationJobDB, ...]:
        if not 1 <= limit <= 1000:
            raise ValueError("speech_adaptation_dispatch_limit_invalid")
        with Session(engine) as session:
            rows = session.exec(
                select(SpeechAdaptationJobDB)
                .where(
                    SpeechAdaptationJobDB.status.in_(_ACTIVE),
                    SpeechAdaptationJobDB.next_dispatch_at_ms <= now_ms,
                )
                .order_by(
                    SpeechAdaptationJobDB.created_at_ms.asc(),
                    SpeechAdaptationJobDB.id.asc(),
                )
                .limit(limit)
            ).all()
            for row in rows:
                session.expunge(row)
            return tuple(rows)

    def list_active(self, *, limit: int) -> tuple[SpeechAdaptationJobDB, ...]:
        if not 1 <= limit <= 1000:
            raise ValueError("speech_adaptation_active_limit_invalid")
        with Session(engine) as session:
            rows = session.exec(
                select(SpeechAdaptationJobDB)
                .where(SpeechAdaptationJobDB.status.in_(_ACTIVE))
                .order_by(
                    SpeechAdaptationJobDB.updated_at_ms.asc(),
                    SpeechAdaptationJobDB.id.asc(),
                )
                .limit(limit)
            ).all()
            for row in rows:
                session.expunge(row)
            return tuple(rows)

    def transition_worker_state(
        self,
        job_id: str,
        *,
        expected_statuses: frozenset[str],
        status: str,
        reason_code: str,
        worker_status: str | None = None,
        result: SpeechAdaptationResult | None = None,
        retry_delay_ms: int = 0,
        increment_dispatch_attempts: bool = False,
    ) -> SpeechAdaptationJobDB:
        if status not in _ACTIVE | _TERMINAL:
            raise ValueError("speech_adaptation_status_invalid")
        with _WRITE_LOCK:
            current = self.get_row(job_id)
            if current is None:
                raise SpeechAdaptationDecisionConflict("speech_job_not_found")
            if current.status not in expected_statuses:
                if current.status == status:
                    return current
                raise SpeechAdaptationDecisionConflict("speech_job_state_conflict")
            now = time.time_ns() // 1_000_000
            values: dict[str, object] = {
                "status": status,
                "reason_code": reason_code,
                "worker_status": worker_status,
                "next_dispatch_at_ms": now + max(0, retry_delay_ms),
                "dispatch_attempts": current.dispatch_attempts + (1 if increment_dispatch_attempts else 0),
                "version": current.version + 1,
                "updated_at_ms": now,
            }
            if result is not None:
                values["result_payload"] = result.to_dict()
            if status in _TERMINAL:
                values["terminal_at_ms"] = now
            with Session(engine) as session:
                changed = session.exec(
                    update(SpeechAdaptationJobDB)
                    .where(
                        SpeechAdaptationJobDB.id == current.id,
                        SpeechAdaptationJobDB.version == current.version,
                        SpeechAdaptationJobDB.status == current.status,
                    )
                    .values(**values)
                )
                if changed.rowcount != 1:
                    session.rollback()
                    raise SpeechAdaptationDecisionConflict("speech_job_state_conflict")
                audit_event = self._audit_event(
                    tenant_id=current.tenant_id,
                    job_id=current.id,
                    version=current.version + 1,
                    status=status,
                    reason_code=reason_code,
                    contract_payload=dict(current.contract_payload or {}),
                )
                if audit_event is not None:
                    SqlSemanticMediaAuditOutbox.enqueue_in_session(session, audit_event)
                session.commit()
            saved = self.get_row(job_id)
            if saved is None:
                raise SpeechAdaptationDecisionConflict("speech_job_not_found")
            return saved

    @staticmethod
    def _scope(principal: SpeechPrincipal):
        return select(SpeechAdaptationJobDB).where(
            SpeechAdaptationJobDB.tenant_id == principal.tenant_id,
            SpeechAdaptationJobDB.owner_subject == principal.subject,
        )

    @staticmethod
    def _replay(
        existing: SpeechAdaptationJobDB,
        decision: SpeechAdmissionDecision,
    ) -> SpeechAdmissionDecision:
        if existing.request_digest != decision.request_digest:
            raise SpeechAdaptationDecisionConflict("speech_idempotency_conflict")
        expected_contract = decision.job.to_dict() if decision.job is not None else {}
        if dict(existing.contract_payload or {}) != expected_contract:
            raise SpeechAdaptationDecisionConflict("speech_idempotency_binding_conflict")
        if dict(existing.admission_request_payload or {}) != dict(decision.admission_request or {}):
            raise SpeechAdaptationDecisionConflict("speech_idempotency_request_binding_conflict")
        return _decision(existing)


class SqlSpeechAdaptationCapacityLeasePort:
    """Atomic SQL slot acquisition shared by all Hub replicas."""

    def __init__(self, *, capacity: int = 1, lease_seconds: int = 300) -> None:
        if not 1 <= capacity <= 128 or not 10 <= lease_seconds <= 3600:
            raise ValueError("speech capacity configuration is invalid")
        self._capacity = capacity
        self._lease_ms = lease_seconds * 1000

    def try_acquire(self, *, job_id: str, deadline_at_ms: int, now_ms: int) -> SpeechCapacityLease | None:
        expires = min(deadline_at_ms, now_ms + self._lease_ms)
        if expires <= now_ms:
            return None
        with _WRITE_LOCK:
            with Session(engine) as session:
                session.exec(
                    delete(SpeechAdaptationCapacityLeaseDB).where(
                        SpeechAdaptationCapacityLeaseDB.expires_at_ms <= now_ms
                    )
                )
                existing = session.exec(
                    select(SpeechAdaptationCapacityLeaseDB).where(SpeechAdaptationCapacityLeaseDB.job_id == job_id)
                ).first()
                session.commit()
                if existing is not None:
                    return _lease(existing)
            for slot in range(self._capacity):
                for _attempt in range(4):
                    # Audit/debug contracts are shared with TypeScript and
                    # therefore keep epochs inside the exact signed-int32
                    # range. Randomness still provides ample collision space
                    # for the bounded (<=128) active lease set.
                    epoch = secrets.randbelow(2**31 - 1) + 1
                    lease_id = f"speech-lease-{hashlib.sha256(f'{job_id}:{epoch}'.encode()).hexdigest()[:32]}"
                    row = SpeechAdaptationCapacityLeaseDB(
                        slot=slot,
                        job_id=job_id,
                        lease_id=lease_id,
                        epoch=epoch,
                        expires_at_ms=expires,
                        created_at_ms=now_ms,
                    )
                    with Session(engine) as session:
                        session.add(row)
                        try:
                            session.commit()
                            return _lease(row)
                        except IntegrityError:
                            session.rollback()
                            existing = session.exec(
                                select(SpeechAdaptationCapacityLeaseDB).where(
                                    SpeechAdaptationCapacityLeaseDB.job_id == job_id
                                )
                            ).first()
                            if existing is not None:
                                return _lease(existing)
                # Another Hub owns this slot; continue to the next one.
            return None

    def release(self, lease_id: str) -> None:
        with _WRITE_LOCK:
            with Session(engine) as session:
                session.exec(
                    delete(SpeechAdaptationCapacityLeaseDB).where(SpeechAdaptationCapacityLeaseDB.lease_id == lease_id)
                )
                session.commit()


class SqlSpeechAdaptationArtifactRepository:
    """Verify and atomically persist bytes supplied by the isolated worker."""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()
        self._root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._root.chmod(0o700)

    def read_committed_adapter(
        self,
        *,
        adapter_id: str,
        tenant_id: str,
        owner_subject: str,
        artifact_ref: str,
        sha256: str,
        size_bytes: int,
        maximum_bytes: int,
    ) -> bytes:
        """Read one registry-bound adapter from the canonical Hub store.

        Export callers cannot turn an opaque artifact reference into an
        arbitrary filesystem read.  Every public binding is re-checked
        against the committed SQL receipt before the internal CAS path is
        resolved.
        """

        if type(maximum_bytes) is not int or not 1 <= size_bytes <= maximum_bytes <= 8 * 1024**3:
            raise SpeechAdaptationDecisionConflict("speech_export_source_size_invalid")
        with Session(engine) as session:
            row = session.exec(
                select(SpeechAdaptationArtifactDB).where(
                    SpeechAdaptationArtifactDB.id == adapter_id,
                    SpeechAdaptationArtifactDB.tenant_id == tenant_id,
                    SpeechAdaptationArtifactDB.owner_subject == owner_subject,
                    SpeechAdaptationArtifactDB.artifact_ref == artifact_ref,
                    SpeechAdaptationArtifactDB.sha256 == sha256,
                    SpeechAdaptationArtifactDB.size_bytes == size_bytes,
                    SpeechAdaptationArtifactDB.media_type == "application/vnd.ananta.speech-adapter",
                    SpeechAdaptationArtifactDB.state == "committed",
                )
            ).first()
            if row is None:
                raise SpeechAdaptationDecisionConflict("speech_export_source_not_committed")
            job_id = row.job_id
            attempt_id = row.attempt_id
            storage_ref = row.storage_ref
        expected_storage_ref = f"hub-artifact://speech-adaptation/{job_id}/{attempt_id}/{sha256}"
        if not secrets.compare_digest(storage_ref, expected_storage_ref):
            raise SpeechAdaptationDecisionConflict("speech_export_source_storage_mismatch")
        source = (self._root / job_id / attempt_id / sha256).resolve()
        try:
            source.relative_to(self._root)
        except ValueError as exc:
            raise SpeechAdaptationDecisionConflict("speech_artifact_storage_boundary") from exc
        try:
            with source.open("rb") as handle:
                payload = handle.read(maximum_bytes + 1)
        except OSError as exc:
            raise SpeechAdaptationDecisionConflict("speech_export_source_unavailable") from exc
        if (
            len(payload) != size_bytes
            or len(payload) > maximum_bytes
            or not secrets.compare_digest(hashlib.sha256(payload).hexdigest(), sha256)
        ):
            raise SpeechAdaptationDecisionConflict("speech_export_source_mismatch")
        return payload

    def publish_encrypted_export(
        self,
        *,
        source_adapter_id: str,
        tenant_id: str,
        owner_subject: str,
        source_artifact_ref: str,
        source_sha256: str,
        source_registry_version: int,
        pair_id: str,
        direction: str,
        speaker_digest: str,
        export_consent_id: str,
        export_consent_digest: str,
        export_consent_scope_digest: str,
        export_consent_session_epoch: int,
        export_consent_version: int,
        export_consent_revocation_epoch: int,
        destination_ref: str,
        payload: bytes,
        media_type: str,
        lineage_nodes=(),
        lineage_edges=(),
        audit_event: SemanticMediaAuditEvent | None = None,
    ) -> tuple[str, int]:
        """Persist an encrypted export in the existing artifact SQL/CAS SOT."""

        if (
            not destination_ref.startswith("artifact://speech-adapter-exports/")
            or ".." in destination_ref.split("/")
            or len(destination_ref) > 512
            or media_type != "application/vnd.ananta.speech-adapter-export+json"
            or not isinstance(payload, bytes)
            or not 1 <= len(payload) <= 8 * 1024**3
        ):
            raise SpeechAdaptationDecisionConflict("speech_export_target_invalid")
        digest = hashlib.sha256(payload).hexdigest()
        export_id = f"speech-export-{hashlib.sha256((destination_ref + digest).encode()).hexdigest()[:32]}"
        export_attempt_id = f"export-{hashlib.sha256(destination_ref.encode()).hexdigest()[:32]}"
        with _WRITE_LOCK, Session(engine) as session:
            source = session.exec(
                select(SpeechAdaptationArtifactDB).where(
                    SpeechAdaptationArtifactDB.id == source_adapter_id,
                    SpeechAdaptationArtifactDB.tenant_id == tenant_id,
                    SpeechAdaptationArtifactDB.owner_subject == owner_subject,
                    SpeechAdaptationArtifactDB.artifact_ref == source_artifact_ref,
                    SpeechAdaptationArtifactDB.sha256 == source_sha256,
                    SpeechAdaptationArtifactDB.media_type == "application/vnd.ananta.speech-adapter",
                    SpeechAdaptationArtifactDB.state == "committed",
                )
            ).first()
            if source is None:
                raise SpeechAdaptationDecisionConflict("speech_export_source_not_committed")
            existing = session.exec(
                select(SpeechAdaptationArtifactDB).where(
                    SpeechAdaptationArtifactDB.tenant_id == tenant_id,
                    SpeechAdaptationArtifactDB.owner_subject == owner_subject,
                    SpeechAdaptationArtifactDB.artifact_ref == destination_ref,
                )
            ).first()
            if existing is not None and (
                existing.id,
                existing.job_id,
                existing.sha256,
                existing.size_bytes,
                existing.media_type,
                existing.state,
            ) != (
                export_id,
                source.job_id,
                digest,
                len(payload),
                media_type,
                "committed",
            ):
                raise SpeechAdaptationDecisionConflict("speech_export_receipt_conflict")
            job_id = source.job_id
        destination = (self._root / "exports" / digest).resolve()
        try:
            destination.relative_to(self._root)
        except ValueError as exc:
            raise SpeechAdaptationDecisionConflict("speech_artifact_storage_boundary") from exc
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        destination.parent.chmod(0o700)
        temporary = destination.with_name(f".{destination.name}.{secrets.token_hex(8)}.tmp")
        created_destination = False
        storage_ref = f"hub-artifact://speech-adaptation/exports/{digest}"
        _WRITE_LOCK.acquire()
        try:
            if destination.exists():
                if destination.stat().st_size != len(payload) or _file_sha256(destination) != digest:
                    raise SpeechAdaptationDecisionConflict("speech_export_storage_conflict")
            else:
                with temporary.open("xb") as handle:
                    temporary.chmod(0o600)
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, destination)
                created_destination = True
            row = SpeechAdaptationArtifactDB(
                id=export_id,
                tenant_id=tenant_id,
                owner_subject=owner_subject,
                job_id=job_id,
                attempt_id=export_attempt_id,
                artifact_ref=destination_ref,
                sha256=digest,
                size_bytes=len(payload),
                media_type=media_type,
                storage_ref=storage_ref,
                state="committed",
            )
            with _WRITE_LOCK, Session(engine) as session:
                now_ms = time.time_ns() // 1_000_000
                source_fence = session.exec(
                    select(SpeechAdaptationArtifactDB)
                    .where(
                        SpeechAdaptationArtifactDB.id == source_adapter_id,
                        SpeechAdaptationArtifactDB.tenant_id == tenant_id,
                        SpeechAdaptationArtifactDB.owner_subject == owner_subject,
                        SpeechAdaptationArtifactDB.job_id == job_id,
                        SpeechAdaptationArtifactDB.artifact_ref == source_artifact_ref,
                        SpeechAdaptationArtifactDB.sha256 == source_sha256,
                        SpeechAdaptationArtifactDB.media_type
                        == "application/vnd.ananta.speech-adapter",
                        SpeechAdaptationArtifactDB.state == "committed",
                    )
                    .with_for_update()
                ).first()
                if source_fence is None:
                    raise SpeechAdaptationDecisionConflict("speech_export_source_fence_changed")
                adapter_fence = session.exec(
                    select(MlInternSpeechAdapterDB)
                    .where(
                        MlInternSpeechAdapterDB.id == source_adapter_id,
                        MlInternSpeechAdapterDB.tenant_id == tenant_id,
                        MlInternSpeechAdapterDB.owner_subject == owner_subject,
                        MlInternSpeechAdapterDB.pair_id == pair_id,
                        MlInternSpeechAdapterDB.direction == direction,
                        MlInternSpeechAdapterDB.speaker_digest == speaker_digest,
                        MlInternSpeechAdapterDB.registry_version == source_registry_version,
                        MlInternSpeechAdapterDB.status == "approved",
                        MlInternSpeechAdapterDB.artifact_ref == source_artifact_ref,
                        MlInternSpeechAdapterDB.artifact_sha256 == source_sha256,
                        MlInternSpeechAdapterDB.expires_at_ms > now_ms,
                        MlInternSpeechAdapterDB.consent_expires_at_ms > now_ms,
                    )
                    .with_for_update()
                ).first()
                if adapter_fence is None:
                    raise SpeechAdaptationDecisionConflict("speech_export_adapter_fence_changed")
                consent_fence = session.exec(
                    select(SpeechEvidenceConsentDB)
                    .where(
                        SpeechEvidenceConsentDB.id == export_consent_id,
                        SpeechEvidenceConsentDB.tenant_id == tenant_id,
                        SpeechEvidenceConsentDB.owner_subject == owner_subject,
                        SpeechEvidenceConsentDB.pair_id == pair_id,
                        SpeechEvidenceConsentDB.direction == direction,
                        SpeechEvidenceConsentDB.session_id == source_adapter_id,
                        SpeechEvidenceConsentDB.speaker_id == speaker_digest,
                        SpeechEvidenceConsentDB.purpose == "speech_adapter_export",
                        SpeechEvidenceConsentDB.scope_digest == export_consent_scope_digest,
                        SpeechEvidenceConsentDB.consent_digest == export_consent_digest,
                        SpeechEvidenceConsentDB.session_epoch == export_consent_session_epoch,
                        SpeechEvidenceConsentDB.consent_version == export_consent_version,
                        SpeechEvidenceConsentDB.revocation_epoch
                        == export_consent_revocation_epoch,
                        SpeechEvidenceConsentDB.state == "active",
                        SpeechEvidenceConsentDB.expires_at_ms > now_ms,
                    )
                    .with_for_update()
                ).first()
                if consent_fence is None:
                    raise SpeechAdaptationDecisionConflict("speech_export_consent_fence_changed")
                consent_scope = dict(consent_fence.scope_payload or {})
                consent_grants = dict(consent_scope.get("grants") or {})
                consent_classes = set(consent_scope.get("data_classes") or ())
                if (
                    consent_grants.get("export") is not True
                    or not consent_classes
                    or not consent_classes <= {"acoustic_features", "speaker_embedding"}
                    or consent_scope.get("session_id") != source_adapter_id
                    or consent_scope.get("session_epoch") != export_consent_session_epoch
                ):
                    raise SpeechAdaptationDecisionConflict("speech_export_consent_fence_changed")
                existing = session.get(SpeechAdaptationArtifactDB, export_id)
                if existing is None:
                    session.add(row)
                elif (
                    existing.tenant_id,
                    existing.owner_subject,
                    existing.job_id,
                    existing.artifact_ref,
                    existing.sha256,
                    existing.size_bytes,
                    existing.media_type,
                    existing.state,
                ) != (
                    tenant_id,
                    owner_subject,
                    job_id,
                    destination_ref,
                    digest,
                    len(payload),
                    media_type,
                    "committed",
                ):
                    raise SpeechAdaptationDecisionConflict("speech_export_receipt_conflict")
                if lineage_nodes:
                    from agent.repositories.speech_evidence_lineage import SpeechEvidenceLineageRepository

                    SpeechEvidenceLineageRepository().stage(
                        session,
                        tenant_id=tenant_id,
                        owner_subject=owner_subject,
                        nodes=tuple(lineage_nodes),
                        edges=tuple(lineage_edges),
                        now_ms=time.time_ns() // 1_000_000,
                    )
                if audit_event is not None:
                    SqlSemanticMediaAuditOutbox.enqueue_in_session(session, audit_event)
                try:
                    session.commit()
                except IntegrityError as exc:
                    session.rollback()
                    raise SpeechAdaptationDecisionConflict("speech_export_receipt_conflict") from exc
            return digest, len(payload)
        except Exception:
            # The encrypted bytes are written before the SQL/outbox transaction
            # so a committed receipt can never point at a missing artifact.  If
            # that transaction fails, remove only the CAS object created by this
            # call and only while no committed receipt references it.
            if created_destination:
                with Session(engine) as session:
                    referenced = session.exec(
                        select(SpeechAdaptationArtifactDB.id).where(
                            SpeechAdaptationArtifactDB.storage_ref == storage_ref
                        )
                    ).first()
                if referenced is None:
                    destination.unlink(missing_ok=True)
            raise
        finally:
            temporary.unlink(missing_ok=True)
            _WRITE_LOCK.release()

    def publish(
        self,
        *,
        job: SpeechAdaptationJobDB,
        artifact_id: str,
        attempt_id: str,
        artifact_ref: str,
        sha256: str,
        size_bytes: int,
        media_type: str,
        stream: BinaryIO,
    ) -> SpeechAdaptationArtifactDB:
        contract = dict(job.contract_payload or {})
        if attempt_id != str(contract.get("attempt", {}).get("attempt_id") or ""):
            raise SpeechAdaptationDecisionConflict("speech_artifact_attempt_mismatch")
        if len(sha256) != 64 or any(character not in "0123456789abcdef" for character in sha256):
            raise SpeechAdaptationDecisionConflict("speech_artifact_digest_invalid")
        budget = dict(contract.get("budget") or {})
        target = dict(contract.get("artifact_target") or {})
        checkpoint_prefix = f"artifact://speech-checkpoints/{job.id}/{attempt_id}/"
        if media_type == "application/vnd.ananta.speech-adapter":
            if (artifact_id, artifact_ref) != (
                str(target.get("target_id") or ""),
                str(target.get("artifact_ref") or ""),
            ):
                raise SpeechAdaptationDecisionConflict("speech_artifact_target_mismatch")
            maximum_size = int(budget.get("max_artifact_bytes") or 0)
        elif media_type == "application/vnd.ananta.speech-checkpoint":
            if artifact_id != f"speech-checkpoint-{sha256[:32]}" or artifact_ref != f"{checkpoint_prefix}{sha256}":
                raise SpeechAdaptationDecisionConflict("speech_checkpoint_target_mismatch")
            maximum_size = int(budget.get("max_disk_bytes") or 0)
        elif media_type == "application/vnd.ananta.speech-evaluation+json":
            expected_ref = f"artifact://speech-evaluations/{job.id}/{attempt_id}/{sha256}"
            if artifact_id != f"speech-evaluation-{sha256[:32]}" or artifact_ref != expected_ref:
                raise SpeechAdaptationDecisionConflict("speech_evaluation_target_mismatch")
            maximum_size = min(
                int(budget.get("max_artifact_bytes") or 0),
                8 * 1024**2,
            )
        else:
            raise SpeechAdaptationDecisionConflict("speech_artifact_media_type_invalid")
        if type(size_bytes) is not int or not 1 <= size_bytes <= maximum_size:
            raise SpeechAdaptationDecisionConflict("speech_artifact_size_invalid")
        with _WRITE_LOCK:
            with Session(engine) as session:
                existing_media = session.exec(
                    select(SpeechAdaptationArtifactDB).where(
                        SpeechAdaptationArtifactDB.job_id == job.id,
                        SpeechAdaptationArtifactDB.attempt_id == attempt_id,
                        SpeechAdaptationArtifactDB.media_type == media_type,
                    )
                ).first()
                if existing_media is not None and (
                    existing_media.id,
                    existing_media.artifact_ref,
                    existing_media.sha256,
                    existing_media.size_bytes,
                ) != (artifact_id, artifact_ref, sha256, size_bytes):
                    raise SpeechAdaptationDecisionConflict("speech_artifact_receipt_conflict")
        destination = (self._root / job.id / attempt_id / sha256).resolve()
        try:
            destination.relative_to(self._root)
        except ValueError as exc:
            raise SpeechAdaptationDecisionConflict("speech_artifact_storage_boundary") from exc
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        destination.parent.chmod(0o700)
        temporary = destination.with_name(f".{destination.name}.{secrets.token_hex(8)}.tmp")
        digest = hashlib.sha256()
        written = 0
        try:
            with temporary.open("xb") as handle:
                temporary.chmod(0o600)
                while True:
                    chunk = stream.read(min(1024 * 1024, size_bytes - written + 1))
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > size_bytes:
                        raise SpeechAdaptationDecisionConflict("speech_artifact_size_mismatch")
                    digest.update(chunk)
                    handle.write(chunk)
                handle.flush()
                os.fsync(handle.fileno())
            if written != size_bytes or not secrets.compare_digest(digest.hexdigest(), sha256):
                raise SpeechAdaptationDecisionConflict("speech_artifact_digest_mismatch")
            if destination.exists():
                if destination.stat().st_size != size_bytes or _file_sha256(destination) != sha256:
                    raise SpeechAdaptationDecisionConflict("speech_artifact_storage_conflict")
                temporary.unlink(missing_ok=True)
            else:
                os.replace(temporary, destination)
            row = SpeechAdaptationArtifactDB(
                id=artifact_id,
                tenant_id=job.tenant_id,
                owner_subject=job.owner_subject,
                job_id=job.id,
                attempt_id=attempt_id,
                artifact_ref=artifact_ref,
                sha256=sha256,
                size_bytes=size_bytes,
                media_type=media_type,
                storage_ref=f"hub-artifact://speech-adaptation/{job.id}/{attempt_id}/{sha256}",
            )
            with _WRITE_LOCK:
                with Session(engine) as session:
                    existing = session.exec(
                        select(SpeechAdaptationArtifactDB).where(
                            SpeechAdaptationArtifactDB.job_id == job.id,
                            SpeechAdaptationArtifactDB.attempt_id == attempt_id,
                            SpeechAdaptationArtifactDB.artifact_ref == artifact_ref,
                        )
                    ).first()
                    if existing is not None:
                        if (
                            existing.id,
                            existing.sha256,
                            existing.size_bytes,
                            existing.media_type,
                        ) != (artifact_id, sha256, size_bytes, media_type):
                            raise SpeechAdaptationDecisionConflict("speech_artifact_receipt_conflict")
                        session.expunge(existing)
                        return existing
                    session.add(row)
                    try:
                        session.commit()
                    except IntegrityError as exc:
                        session.rollback()
                        conflict = session.exec(
                            select(SpeechAdaptationArtifactDB).where(
                                SpeechAdaptationArtifactDB.job_id == job.id,
                                SpeechAdaptationArtifactDB.attempt_id == attempt_id,
                                SpeechAdaptationArtifactDB.media_type == media_type,
                            )
                        ).first()
                        if conflict is not None and (
                            conflict.id,
                            conflict.artifact_ref,
                            conflict.sha256,
                            conflict.size_bytes,
                        ) == (artifact_id, artifact_ref, sha256, size_bytes):
                            session.expunge(conflict)
                            return conflict
                        raise SpeechAdaptationDecisionConflict("speech_artifact_receipt_conflict") from exc
                    session.refresh(row)
                    session.expunge(row)
                    return row
        finally:
            temporary.unlink(missing_ok=True)

    def verify_and_commit(
        self,
        principal: SpeechPrincipal,
        job,
        result: SpeechAdaptationResult,
    ) -> None:
        """CAS-bind a terminal result to bytes already accepted by the Hub."""

        if result.status == "completed":
            if result.artifact is None:
                raise SpeechAdaptationDecisionConflict("speech_result_artifact_missing")
            with _WRITE_LOCK:
                with Session(engine) as session:
                    artifact = session.exec(
                        select(SpeechAdaptationArtifactDB).where(
                            SpeechAdaptationArtifactDB.tenant_id == principal.tenant_id,
                            SpeechAdaptationArtifactDB.owner_subject == principal.subject,
                            SpeechAdaptationArtifactDB.job_id == job.job_id,
                            SpeechAdaptationArtifactDB.attempt_id == job.attempt.attempt_id,
                            SpeechAdaptationArtifactDB.id == result.artifact.artifact_id,
                            SpeechAdaptationArtifactDB.artifact_ref == result.artifact.artifact_ref,
                            SpeechAdaptationArtifactDB.sha256 == result.artifact.sha256,
                            SpeechAdaptationArtifactDB.size_bytes == result.artifact.size_bytes,
                            SpeechAdaptationArtifactDB.media_type == result.artifact.media_type,
                            SpeechAdaptationArtifactDB.state.in_({"pending", "committed"}),
                        )
                    ).first()
                    if artifact is None:
                        raise SpeechAdaptationDecisionConflict("speech_result_artifact_not_published_by_hub")
                    if result.checkpoint_digest is not None:
                        checkpoint = session.exec(
                            select(SpeechAdaptationArtifactDB).where(
                                SpeechAdaptationArtifactDB.tenant_id == principal.tenant_id,
                                SpeechAdaptationArtifactDB.owner_subject == principal.subject,
                                SpeechAdaptationArtifactDB.job_id == job.job_id,
                                SpeechAdaptationArtifactDB.attempt_id == job.attempt.attempt_id,
                                SpeechAdaptationArtifactDB.sha256 == result.checkpoint_digest,
                                SpeechAdaptationArtifactDB.media_type == "application/vnd.ananta.speech-checkpoint",
                                SpeechAdaptationArtifactDB.state.in_({"pending", "checkpointed"}),
                            )
                        ).first()
                        if checkpoint is None:
                            raise SpeechAdaptationDecisionConflict("speech_result_checkpoint_not_published_by_hub")
                        if checkpoint.state == "pending":
                            checkpoint.state = "checkpointed"
                            checkpoint.updated_at_ms = time.time_ns() // 1_000_000
                            session.add(checkpoint)
                    evaluation = session.exec(
                        select(SpeechAdaptationArtifactDB).where(
                            SpeechAdaptationArtifactDB.tenant_id == principal.tenant_id,
                            SpeechAdaptationArtifactDB.owner_subject == principal.subject,
                            SpeechAdaptationArtifactDB.job_id == job.job_id,
                            SpeechAdaptationArtifactDB.attempt_id == job.attempt.attempt_id,
                            SpeechAdaptationArtifactDB.sha256 == result.evaluation_report_digest,
                            SpeechAdaptationArtifactDB.media_type == "application/vnd.ananta.speech-evaluation+json",
                            SpeechAdaptationArtifactDB.state.in_({"pending", "evaluated"}),
                        )
                    ).first()
                    if evaluation is None:
                        raise SpeechAdaptationDecisionConflict("speech_result_evaluation_not_published_by_hub")
                    if evaluation.state == "pending":
                        evaluation.state = "evaluated"
                        evaluation.updated_at_ms = time.time_ns() // 1_000_000
                        session.add(evaluation)
                    if artifact.state == "pending":
                        artifact.state = "committed"
                        artifact.updated_at_ms = time.time_ns() // 1_000_000
                        session.add(artifact)
                    session.commit()
            return
        self.reject_attempt(principal, job)

    def read_evaluation(
        self,
        principal: SpeechPrincipal,
        job,
        evaluation_digest: str,
    ) -> dict:
        with Session(engine) as session:
            row = session.exec(
                select(SpeechAdaptationArtifactDB).where(
                    SpeechAdaptationArtifactDB.tenant_id == principal.tenant_id,
                    SpeechAdaptationArtifactDB.owner_subject == principal.subject,
                    SpeechAdaptationArtifactDB.job_id == job.job_id,
                    SpeechAdaptationArtifactDB.attempt_id == job.attempt.attempt_id,
                    SpeechAdaptationArtifactDB.sha256 == evaluation_digest,
                    SpeechAdaptationArtifactDB.media_type == "application/vnd.ananta.speech-evaluation+json",
                    SpeechAdaptationArtifactDB.state == "evaluated",
                )
            ).first()
            if row is None:
                raise SpeechAdaptationDecisionConflict("speech_evaluation_report_not_available")
            size_bytes = row.size_bytes
        path = (self._root / job.job_id / job.attempt.attempt_id / evaluation_digest).resolve()
        try:
            path.relative_to(self._root)
            content = path.read_bytes()
        except (ValueError, OSError) as exc:
            raise SpeechAdaptationDecisionConflict("speech_evaluation_report_storage_invalid") from exc
        if (
            len(content) != size_bytes
            or len(content) > min(job.budget.max_artifact_bytes, 8 * 1024**2)
            or hashlib.sha256(content).hexdigest() != evaluation_digest
        ):
            raise SpeechAdaptationDecisionConflict("speech_evaluation_report_storage_invalid")
        try:
            payload = json.loads(content.decode("utf-8"), parse_constant=_reject_constant)
        except (UnicodeError, ValueError) as exc:
            raise SpeechAdaptationDecisionConflict("speech_evaluation_report_storage_invalid") from exc
        if not isinstance(payload, dict):
            raise SpeechAdaptationDecisionConflict("speech_evaluation_report_storage_invalid")
        return payload

    def reject_attempt(self, principal: SpeechPrincipal, job) -> None:
        with _WRITE_LOCK:
            with Session(engine) as session:
                rows = session.exec(
                    select(SpeechAdaptationArtifactDB).where(
                        SpeechAdaptationArtifactDB.tenant_id == principal.tenant_id,
                        SpeechAdaptationArtifactDB.owner_subject == principal.subject,
                        SpeechAdaptationArtifactDB.job_id == job.job_id,
                        SpeechAdaptationArtifactDB.attempt_id == job.attempt.attempt_id,
                        SpeechAdaptationArtifactDB.state == "pending",
                    )
                ).all()
                for row in rows:
                    destination = (self._root / row.job_id / row.attempt_id / row.sha256).resolve()
                    try:
                        destination.relative_to(self._root)
                    except ValueError as exc:
                        raise SpeechAdaptationDecisionConflict("speech_artifact_storage_boundary") from exc
                    try:
                        destination.unlink(missing_ok=True)
                    except OSError as exc:
                        raise SpeechAdaptationDecisionConflict("speech_artifact_rejection_cleanup_failed") from exc
                    row.state = "rejected"
                    row.updated_at_ms = time.time_ns() // 1_000_000
                    session.add(row)
                session.commit()


def _decision(row: SpeechAdaptationJobDB) -> SpeechAdmissionDecision:
    payload = dict(row.contract_payload or {})
    result_payload = dict(row.result_payload or {})
    return SpeechAdmissionDecision(
        job_id=row.id,
        task_id=row.task_id,
        status=row.status,
        reason_code=row.reason_code,
        job=restore_speech_adaptation_job(payload) if payload else None,
        request_digest=row.request_digest,
        admission_request=dict(row.admission_request_payload or {}) or None,
        result=(SpeechAdaptationResult.from_mapping(result_payload) if result_payload else None),
    )


def _lease(row: SpeechAdaptationCapacityLeaseDB) -> SpeechCapacityLease:
    return SpeechCapacityLease(row.lease_id, row.epoch, row.expires_at_ms)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _reject_constant(_value: str) -> None:
    raise ValueError("non-finite JSON is forbidden")


__all__ = [
    "SqlSpeechAdaptationArtifactRepository",
    "SqlSpeechAdaptationCapacityLeasePort",
    "SqlSpeechAdaptationDecisionStore",
]
