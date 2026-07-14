"""Durable SQL repository for the Hub workflow-runtime operations projection."""

from __future__ import annotations

import hashlib
from copy import deepcopy

import sqlalchemy as sa
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from agent.db_models.workflow_runtime import WorkflowRuntimeReadModelDB
from agent.services.identity_validation import require_canonical_identity
from agent.services.workflow_runtime_operations_models import WorkflowRuntimeOperationRecord


class SQLAlchemyWorkflowRuntimeReadModelRepository:
    """Tenant-scoped, restart-safe read model with monotonic CAS updates."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def upsert(self, record: WorkflowRuntimeOperationRecord) -> WorkflowRuntimeOperationRecord:
        record = record.validated_copy()
        payload = _serialize(record)
        row_id = _row_id(record.tenant_id, record.run_id)
        for _ in range(2):
            try:
                with Session(self._engine) as session:
                    current = session.get(WorkflowRuntimeReadModelDB, row_id)
                    if current is None:
                        session.add(
                            WorkflowRuntimeReadModelDB(
                                id=row_id,
                                tenant_id=record.tenant_id,
                                run_id=record.run_id,
                                workflow_id=record.workflow_id,
                                runtime=record.runtime,
                                mode=record.mode,
                                status=record.status,
                                source_sequence=record.source_sequence,
                                updated_at=record.updated_at,
                                record=payload,
                            )
                        )
                        session.commit()
                        return record

                    existing = _deserialize(current)
                    if record.source_sequence < current.source_sequence:
                        raise ValueError("runtime_read_model_sequence_regression")
                    if (
                        record.source_sequence == current.source_sequence
                        and record.updated_at < current.updated_at
                    ):
                        return existing

                    result = session.exec(
                        sa.update(WorkflowRuntimeReadModelDB)
                        .where(
                            WorkflowRuntimeReadModelDB.id == row_id,
                            sa.or_(
                                WorkflowRuntimeReadModelDB.source_sequence
                                < record.source_sequence,
                                sa.and_(
                                    WorkflowRuntimeReadModelDB.source_sequence
                                    == record.source_sequence,
                                    WorkflowRuntimeReadModelDB.updated_at
                                    <= record.updated_at,
                                ),
                            ),
                        )
                        .values(
                            workflow_id=record.workflow_id,
                            runtime=record.runtime,
                            mode=record.mode,
                            status=record.status,
                            source_sequence=record.source_sequence,
                            updated_at=record.updated_at,
                            record=payload,
                        )
                    )
                    if int(result.rowcount or 0) == 1:
                        session.commit()
                        return record
                    session.rollback()
                    refreshed = session.get(WorkflowRuntimeReadModelDB, row_id)
                    if refreshed is None:
                        continue
                    if record.source_sequence < refreshed.source_sequence:
                        raise ValueError("runtime_read_model_sequence_regression")
                    return _deserialize(refreshed)
            except IntegrityError:
                # A concurrent first observation won the unique tenant/run row.
                # Retry once and apply the same monotonic CAS rules to that row.
                continue
        raise RuntimeError("runtime_read_model_concurrent_upsert_failed")

    def get(self, *, tenant_id: str, run_id: str) -> WorkflowRuntimeOperationRecord | None:
        validated_tenant = require_canonical_identity(tenant_id, field_name="tenant_id")
        validated_run = require_canonical_identity(run_id, field_name="run_id")
        with Session(self._engine) as session:
            row = session.exec(
                select(WorkflowRuntimeReadModelDB).where(
                    WorkflowRuntimeReadModelDB.tenant_id == validated_tenant,
                    WorkflowRuntimeReadModelDB.run_id == validated_run,
                )
            ).first()
            return _deserialize(row) if row is not None else None

    def list_for_tenant(
        self,
        *,
        tenant_id: str,
    ) -> tuple[WorkflowRuntimeOperationRecord, ...]:
        validated_tenant = require_canonical_identity(tenant_id, field_name="tenant_id")
        with Session(self._engine) as session:
            rows = session.exec(
                select(WorkflowRuntimeReadModelDB)
                .where(WorkflowRuntimeReadModelDB.tenant_id == validated_tenant)
                .order_by(
                    WorkflowRuntimeReadModelDB.updated_at.desc(),
                    WorkflowRuntimeReadModelDB.run_id,
                )
            ).all()
            return tuple(_deserialize(row) for row in rows)

    def clear(self) -> None:
        """Explicit test/support hook; never used by request handling."""

        if not sa.inspect(self._engine).has_table(WorkflowRuntimeReadModelDB.__tablename__):
            return
        with Session(self._engine) as session:
            session.exec(sa.delete(WorkflowRuntimeReadModelDB))
            session.commit()


def _serialize(record: WorkflowRuntimeOperationRecord) -> dict[str, object]:
    payload = dict(record.to_dict(now=record.updated_at))
    payload.update(
        {
            "tenant_id": record.tenant_id,
            # ``degraded`` is evaluated from several fields for the UI.  Only
            # the explicit runtime assertion may be restored as explicit state.
            "degraded": record.explicitly_degraded,
        }
    )
    return deepcopy(payload)


def _deserialize(row: WorkflowRuntimeReadModelDB) -> WorkflowRuntimeOperationRecord:
    payload = deepcopy(dict(row.record))
    payload["tenant_id"] = str(row.tenant_id)
    payload["run_id"] = str(row.run_id)
    payload["workflow_id"] = str(row.workflow_id)
    payload["runtime"] = str(row.runtime)
    payload["mode"] = str(row.mode)
    payload["status"] = str(row.status)
    payload["source_sequence"] = int(row.source_sequence)
    payload["updated_at"] = float(row.updated_at)
    return WorkflowRuntimeOperationRecord.from_mapping(payload)


def _row_id(tenant_id: str, run_id: str) -> str:
    digest = hashlib.sha256(f"{tenant_id}\0{run_id}".encode("utf-8")).hexdigest()
    return f"workflow-runtime-read-{digest}"


__all__ = ["SQLAlchemyWorkflowRuntimeReadModelRepository"]
