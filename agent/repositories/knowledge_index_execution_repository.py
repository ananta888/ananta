"""SQLModel CAS repository for Hub-owned knowledge-index execution bindings."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from agent.db_models.knowledge_index_execution import (
    KnowledgeIndexExecutionBindingDB,
)
from agent.services.knowledge_index_execution_binding_service import (
    KnowledgeIndexExecutionBindingError,
    KnowledgeIndexExecutionRecord,
)
from ananta_contracts.knowledge_index_execution import (
    KnowledgeIndexExecutionJob,
)


class SQLKnowledgeIndexExecutionRepository:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def admit(
        self,
        record: KnowledgeIndexExecutionRecord,
    ) -> tuple[KnowledgeIndexExecutionRecord, bool]:
        job = record.job
        with Session(self._engine) as db:
            existing = db.exec(
                select(KnowledgeIndexExecutionBindingDB).where(
                    KnowledgeIndexExecutionBindingDB.tenant_id
                    == job.authority_binding.tenant_id,
                    KnowledgeIndexExecutionBindingDB.project_id
                    == job.authority_binding.project_id,
                    KnowledgeIndexExecutionBindingDB.idempotency_key_digest
                    == job.idempotency_key_digest,
                )
            ).first()
            if existing is not None:
                loaded = self._record(existing)
                if (
                    loaded.job.idempotency_fingerprint
                    != job.idempotency_fingerprint
                ):
                    raise KnowledgeIndexExecutionBindingError(
                        "knowledge_index_execution_idempotency_conflict"
                    )
                return loaded, False
            row = self._row(record)
            db.add(row)
            try:
                db.commit()
            except IntegrityError:
                db.rollback()
                raise KnowledgeIndexExecutionBindingError(
                    "knowledge_index_execution_idempotency_conflict"
                ) from None
            db.refresh(row)
            return self._record(row), True

    def get(self, job_id: str) -> KnowledgeIndexExecutionRecord | None:
        with Session(self._engine) as db:
            row = db.get(KnowledgeIndexExecutionBindingDB, job_id)
            return None if row is None else self._record(row)

    def get_by_assignment(
        self,
        *,
        assignment_id: str,
        lease_id: str,
    ) -> KnowledgeIndexExecutionRecord | None:
        with Session(self._engine) as db:
            rows = list(
                db.exec(
                    select(KnowledgeIndexExecutionBindingDB).where(
                        KnowledgeIndexExecutionBindingDB.assignment_id
                        == assignment_id,
                        KnowledgeIndexExecutionBindingDB.lease_id
                        == lease_id,
                    ).limit(2)
                ).all()
            )
            if len(rows) != 1:
                return None
            return self._record(rows[0])

    def compare_and_set(
        self,
        record: KnowledgeIndexExecutionRecord,
        *,
        expected_lock_version: int,
    ) -> KnowledgeIndexExecutionRecord:
        row = self._row(record)
        values = {
            column.name: getattr(row, column.name)
            for column in KnowledgeIndexExecutionBindingDB.__table__.columns
            if column.name != "job_id"
        }
        with Session(self._engine) as db:
            result = db.execute(
                update(KnowledgeIndexExecutionBindingDB)
                .where(
                    KnowledgeIndexExecutionBindingDB.job_id
                    == record.job.job_id,
                    KnowledgeIndexExecutionBindingDB.lock_version
                    == expected_lock_version,
                )
                .values(**values)
            )
            if result.rowcount != 1:
                db.rollback()
                raise KnowledgeIndexExecutionBindingError(
                    "knowledge_index_execution_version_conflict"
                )
            db.commit()
            refreshed = db.get(
                KnowledgeIndexExecutionBindingDB,
                record.job.job_id,
            )
            if refreshed is None:
                raise KnowledgeIndexExecutionBindingError(
                    "knowledge_index_execution_not_found"
                )
            return self._record(refreshed)

    @staticmethod
    def _row(
        record: KnowledgeIndexExecutionRecord,
    ) -> KnowledgeIndexExecutionBindingDB:
        job = record.job
        binding = job.authority_binding
        assignment = job.assignment
        return KnowledgeIndexExecutionBindingDB(
            job_id=job.job_id,
            hub_task_id=job.hub_task_id,
            tenant_id=binding.tenant_id,
            project_id=binding.project_id,
            owner_id=record.owner_id,
            idempotency_key_digest=job.idempotency_key_digest,
            idempotency_fingerprint=job.idempotency_fingerprint,
            source_revision_id=binding.source_revision_id,
            source_revision_digest=binding.source_revision_digest,
            admission_digest=binding.admission_digest,
            policy_snapshot_id=binding.policy_snapshot_id,
            policy_snapshot_digest=binding.policy_snapshot_digest,
            destination_id=binding.destination_id,
            destination_digest=binding.destination_digest,
            source_access_grant_id=binding.source_access_grant_id,
            source_access_grant_digest=(
                binding.source_access_grant_digest
            ),
            authority_binding_digest=binding.binding_digest,
            file_manifest_digest=job.file_manifest.manifest_digest,
            assignment_id=assignment.assignment_id,
            assigned_worker_id=assignment.worker_id,
            lease_id=assignment.lease_id,
            lease_generation=assignment.lease_generation,
            lease_expires_epoch_ms=assignment.lease_expires_epoch_ms,
            attempt=job.attempt,
            state=record.state,
            lock_version=record.lock_version,
            envelope_json=job.to_wire(),
            result_digest=record.result_digest,
            created_at_epoch_ms=job.created_at_epoch_ms,
            updated_at_epoch_ms=record.updated_at_epoch_ms,
            completed_at_epoch_ms=record.completed_at_epoch_ms,
        )

    @staticmethod
    def _record(
        row: KnowledgeIndexExecutionBindingDB,
    ) -> KnowledgeIndexExecutionRecord:
        return KnowledgeIndexExecutionRecord(
            job=KnowledgeIndexExecutionJob.model_validate(
                dict(row.envelope_json)
            ),
            owner_id=row.owner_id,
            state=row.state,
            lock_version=row.lock_version,
            result_digest=row.result_digest,
            updated_at_epoch_ms=row.updated_at_epoch_ms,
            completed_at_epoch_ms=row.completed_at_epoch_ms,
        )
