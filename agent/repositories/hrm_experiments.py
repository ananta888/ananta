"""SQL-backed, scope-safe repository for the HRM Hub control plane."""

from __future__ import annotations

import threading
import time
from typing import Any, Mapping

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, func, select

from agent.database import engine as default_engine
from agent.db_models.hrm_experiment_idempotency import HrmIdempotencyReceiptDB
from agent.db_models import (
    HrmCheckpointDB,
    HrmDatasetDB,
    HrmEvaluationReportDB,
    HrmRunDB,
    HrmRunEventDB,
    HrmWorkerCapabilityDB,
)

_WRITE_LOCK = threading.RLock()


class HrmRepositoryConflict(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class HrmExperimentRepository:
    def __init__(self, *, db_engine=default_engine, clock=time.time) -> None:
        self._engine = db_engine
        self._clock = clock

    def upsert_capability(
        self,
        *,
        worker_id: str,
        worker_url: str,
        projection: Mapping[str, Any],
        expires_at: float,
    ) -> HrmWorkerCapabilityDB:
        now = self._clock()
        with _WRITE_LOCK, Session(self._engine) as session:
            item = session.exec(
                select(HrmWorkerCapabilityDB).where(
                    HrmWorkerCapabilityDB.worker_url == worker_url
                )
            ).first()
            if item is None:
                item = HrmWorkerCapabilityDB(
                    worker_id=worker_id,
                    worker_url=worker_url,
                    capability_digest=str(projection["capability_digest"]),
                    projection=dict(projection),
                    observed_at=now,
                    expires_at=expires_at,
                )
            else:
                item.worker_id = worker_id
                item.capability_digest = str(projection["capability_digest"])
                item.projection = dict(projection)
                item.observed_at = now
                item.expires_at = expires_at
                item.version += 1
            session.add(item)
            session.commit()
            session.refresh(item)
            return item

    def current_capability(self, *, worker_url: str | None = None) -> HrmWorkerCapabilityDB | None:
        with Session(self._engine) as session:
            statement = select(HrmWorkerCapabilityDB).where(
                HrmWorkerCapabilityDB.expires_at > self._clock()
            )
            if worker_url is not None:
                statement = statement.where(HrmWorkerCapabilityDB.worker_url == worker_url)
            return session.exec(
                statement.order_by(HrmWorkerCapabilityDB.observed_at.desc())
            ).first()

    def claim_idempotency(
        self,
        *,
        tenant_id: str,
        owner_subject: str,
        operation: str,
        key_digest: str,
        request_digest: str,
    ) -> tuple[HrmIdempotencyReceiptDB, bool]:
        """Claim a mutation key or return its durable replay projection."""

        with _WRITE_LOCK, Session(self._engine) as session:
            statement = select(HrmIdempotencyReceiptDB).where(
                HrmIdempotencyReceiptDB.tenant_id == tenant_id,
                HrmIdempotencyReceiptDB.owner_subject == owner_subject,
                HrmIdempotencyReceiptDB.operation == operation,
                HrmIdempotencyReceiptDB.key_digest == key_digest,
            )
            existing = session.exec(statement).first()
            if existing is not None:
                if existing.request_digest != request_digest:
                    raise HrmRepositoryConflict("hrm.idempotency_payload_conflict")
                return existing, True
            item = HrmIdempotencyReceiptDB(
                tenant_id=tenant_id,
                owner_subject=owner_subject,
                operation=operation,
                key_digest=key_digest,
                request_digest=request_digest,
            )
            session.add(item)
            try:
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                existing = session.exec(statement).first()
                if existing is None or existing.request_digest != request_digest:
                    raise HrmRepositoryConflict(
                        "hrm.idempotency_payload_conflict"
                    ) from exc
                return existing, True
            session.refresh(item)
            return item, False

    def complete_idempotency(
        self,
        receipt_id: str,
        *,
        request_digest: str,
        resource_id: str,
        response: Mapping[str, Any],
    ) -> HrmIdempotencyReceiptDB:
        with _WRITE_LOCK, Session(self._engine) as session:
            item = session.get(HrmIdempotencyReceiptDB, receipt_id)
            if item is None or item.request_digest != request_digest:
                raise HrmRepositoryConflict("hrm.idempotency_receipt_conflict")
            item.state = "completed"
            item.resource_id = resource_id
            item.response = dict(response)
            item.updated_at = self._clock()
            session.add(item)
            session.commit()
            session.refresh(item)
            return item

    def release_idempotency(self, receipt_id: str, *, request_digest: str) -> None:
        with _WRITE_LOCK, Session(self._engine) as session:
            item = session.get(HrmIdempotencyReceiptDB, receipt_id)
            if (
                item is not None
                and item.state == "pending"
                and item.request_digest == request_digest
            ):
                session.delete(item)
                session.commit()

    def save_dataset(
        self,
        manifest: Mapping[str, Any],
        records: list[Any],
        *,
        owner_subject: str,
    ) -> HrmDatasetDB:
        scope = manifest["scope"]
        item = HrmDatasetDB(
            dataset_id=str(manifest["dataset_id"]),
            tenant_id=str(scope["tenant_id"]),
            project_id=str(scope["project_id"]),
            owner_subject=owner_subject,
            puzzle_type=str(manifest["puzzle_type"]),
            content_digest=str(manifest["canonical_content_digest"]),
            manifest=dict(manifest),
            records=list(records),
        )
        with _WRITE_LOCK, Session(self._engine) as session:
            existing = session.exec(
                self._dataset_scope(item.tenant_id, item.project_id).where(
                    (HrmDatasetDB.dataset_id == item.dataset_id)
                    | (HrmDatasetDB.content_digest == item.content_digest)
                )
            ).first()
            if existing is not None:
                if existing.manifest != item.manifest:
                    raise HrmRepositoryConflict("hrm.dataset_conflict")
                return existing
            session.add(item)
            try:
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                raise HrmRepositoryConflict("hrm.dataset_conflict") from exc
            session.refresh(item)
            return item

    def get_dataset(self, tenant_id: str, project_id: str, dataset_id: str) -> HrmDatasetDB | None:
        with Session(self._engine) as session:
            return session.exec(
                self._dataset_scope(tenant_id, project_id).where(
                    HrmDatasetDB.dataset_id == dataset_id
                )
            ).first()

    def list_datasets(self, tenant_id: str, project_id: str, *, offset: int, limit: int) -> list[HrmDatasetDB]:
        with Session(self._engine) as session:
            return list(
                session.exec(
                    self._dataset_scope(tenant_id, project_id)
                    .order_by(HrmDatasetDB.created_at.asc(), HrmDatasetDB.id.asc())
                    .offset(offset)
                    .limit(limit)
                ).all()
            )

    def save_checkpoint(self, manifest: Mapping[str, Any], *, owner_subject: str) -> HrmCheckpointDB:
        scope = manifest["scope"]
        item = HrmCheckpointDB(
            checkpoint_id=str(manifest["checkpoint_id"]),
            tenant_id=str(scope["tenant_id"]),
            project_id=str(scope["project_id"]),
            owner_subject=owner_subject,
            content_digest=str(manifest["content_digest"]),
            state=str(manifest["state"]),
            manifest=dict(manifest),
        )
        with _WRITE_LOCK, Session(self._engine) as session:
            existing = session.exec(
                self._checkpoint_scope(item.tenant_id, item.project_id).where(
                    (HrmCheckpointDB.checkpoint_id == item.checkpoint_id)
                    | (HrmCheckpointDB.content_digest == item.content_digest)
                )
            ).first()
            if existing is not None:
                if existing.manifest != item.manifest:
                    raise HrmRepositoryConflict("hrm.checkpoint_conflict")
                return existing
            session.add(item)
            try:
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                raise HrmRepositoryConflict("hrm.checkpoint_conflict") from exc
            session.refresh(item)
            return item

    def get_checkpoint(self, tenant_id: str, project_id: str, checkpoint_id: str) -> HrmCheckpointDB | None:
        with Session(self._engine) as session:
            return session.exec(
                self._checkpoint_scope(tenant_id, project_id).where(
                    HrmCheckpointDB.checkpoint_id == checkpoint_id
                )
            ).first()

    def list_checkpoints(self, tenant_id: str, project_id: str, *, offset: int, limit: int) -> list[HrmCheckpointDB]:
        with Session(self._engine) as session:
            return list(
                session.exec(
                    self._checkpoint_scope(tenant_id, project_id)
                    .order_by(HrmCheckpointDB.created_at.asc(), HrmCheckpointDB.id.asc())
                    .offset(offset)
                    .limit(limit)
                ).all()
            )

    def create_run(self, run: HrmRunDB) -> tuple[HrmRunDB, bool]:
        with _WRITE_LOCK, Session(self._engine) as session:
            existing = session.exec(
                select(HrmRunDB).where(
                    HrmRunDB.tenant_id == run.tenant_id,
                    HrmRunDB.owner_subject == run.owner_subject,
                    HrmRunDB.idempotency_key_digest == run.idempotency_key_digest,
                )
            ).first()
            if existing is not None:
                if existing.request_digest != run.request_digest:
                    raise HrmRepositoryConflict("hrm.idempotency_payload_conflict")
                return existing, True
            session.add(run)
            try:
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                existing = session.exec(
                    select(HrmRunDB).where(
                        HrmRunDB.tenant_id == run.tenant_id,
                        HrmRunDB.owner_subject == run.owner_subject,
                        HrmRunDB.idempotency_key_digest == run.idempotency_key_digest,
                    )
                ).first()
                if existing is None or existing.request_digest != run.request_digest:
                    raise HrmRepositoryConflict("hrm.idempotency_payload_conflict") from exc
                return existing, True
            session.refresh(run)
            return run, False

    def get_run(self, tenant_id: str, project_id: str, run_id: str) -> HrmRunDB | None:
        with Session(self._engine) as session:
            return session.exec(
                self._run_scope(tenant_id, project_id).where(HrmRunDB.id == run_id)
            ).first()

    def get_run_internal(self, run_id: str) -> HrmRunDB | None:
        with Session(self._engine) as session:
            return session.get(HrmRunDB, run_id)

    def list_runs(self, tenant_id: str, project_id: str, *, offset: int, limit: int) -> list[HrmRunDB]:
        with Session(self._engine) as session:
            return list(
                session.exec(
                    self._run_scope(tenant_id, project_id)
                    .order_by(HrmRunDB.created_at.asc(), HrmRunDB.id.asc())
                    .offset(offset)
                    .limit(limit)
                ).all()
            )

    def save_run(self, run: HrmRunDB) -> HrmRunDB:
        run.updated_at = self._clock()
        run.version += 1
        with _WRITE_LOCK, Session(self._engine) as session:
            session.add(run)
            session.commit()
            session.refresh(run)
            return run

    def append_event(self, run: HrmRunDB, event: Mapping[str, Any]) -> HrmRunEventDB:
        with _WRITE_LOCK, Session(self._engine) as session:
            maximum = session.exec(
                select(func.max(HrmRunEventDB.sequence)).where(HrmRunEventDB.run_id == run.id)
            ).one()
            sequence = int(maximum or 0) + 1
            payload = dict(event)
            payload["sequence"] = sequence
            item = HrmRunEventDB(
                run_id=run.id,
                tenant_id=run.tenant_id,
                project_id=run.project_id,
                sequence=sequence,
                event=payload,
            )
            session.add(item)
            session.commit()
            session.refresh(item)
            return item

    def list_events(self, tenant_id: str, project_id: str, run_id: str, *, after: int, limit: int) -> list[HrmRunEventDB]:
        with Session(self._engine) as session:
            return list(
                session.exec(
                    select(HrmRunEventDB)
                    .where(
                        HrmRunEventDB.tenant_id == tenant_id,
                        HrmRunEventDB.project_id == project_id,
                        HrmRunEventDB.run_id == run_id,
                        HrmRunEventDB.sequence > after,
                    )
                    .order_by(HrmRunEventDB.sequence.asc())
                    .limit(limit)
                ).all()
            )

    def last_event_sequence(
        self, tenant_id: str, project_id: str, run_id: str
    ) -> int:
        with Session(self._engine) as session:
            maximum = session.exec(
                select(func.max(HrmRunEventDB.sequence)).where(
                    HrmRunEventDB.tenant_id == tenant_id,
                    HrmRunEventDB.project_id == project_id,
                    HrmRunEventDB.run_id == run_id,
                )
            ).one()
            return int(maximum or 0)

    def create_report(self, report: HrmEvaluationReportDB) -> tuple[HrmEvaluationReportDB, bool]:
        with _WRITE_LOCK, Session(self._engine) as session:
            existing = session.exec(
                select(HrmEvaluationReportDB).where(
                    HrmEvaluationReportDB.tenant_id == report.tenant_id,
                    HrmEvaluationReportDB.owner_subject == report.owner_subject,
                    HrmEvaluationReportDB.idempotency_key_digest == report.idempotency_key_digest,
                )
            ).first()
            if existing is not None:
                if existing.request_digest != report.request_digest:
                    raise HrmRepositoryConflict("hrm.idempotency_payload_conflict")
                return existing, True
            session.add(report)
            try:
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                raise HrmRepositoryConflict("hrm.report_conflict") from exc
            session.refresh(report)
            return report, False

    def get_report(self, tenant_id: str, project_id: str, report_id: str) -> HrmEvaluationReportDB | None:
        with Session(self._engine) as session:
            return session.exec(
                select(HrmEvaluationReportDB).where(
                    HrmEvaluationReportDB.tenant_id == tenant_id,
                    HrmEvaluationReportDB.project_id == project_id,
                    HrmEvaluationReportDB.id == report_id,
                )
            ).first()

    @staticmethod
    def _dataset_scope(tenant_id: str, project_id: str):
        return select(HrmDatasetDB).where(
            HrmDatasetDB.tenant_id == tenant_id,
            HrmDatasetDB.project_id == project_id,
        )

    @staticmethod
    def _checkpoint_scope(tenant_id: str, project_id: str):
        return select(HrmCheckpointDB).where(
            HrmCheckpointDB.tenant_id == tenant_id,
            HrmCheckpointDB.project_id == project_id,
        )

    @staticmethod
    def _run_scope(tenant_id: str, project_id: str):
        return select(HrmRunDB).where(
            HrmRunDB.tenant_id == tenant_id,
            HrmRunDB.project_id == project_id,
        )


_default_repository: HrmExperimentRepository | None = None


def get_hrm_experiment_repository() -> HrmExperimentRepository:
    global _default_repository
    if _default_repository is None:
        _default_repository = HrmExperimentRepository()
    return _default_repository


__all__ = ["HrmExperimentRepository", "HrmRepositoryConflict", "get_hrm_experiment_repository"]
