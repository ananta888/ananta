"""SQL outbox repository for Source Control job events.
"""

from __future__ import annotations

import time
from collections.abc import Sequence

from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from agent.db_models.source_control import SourceControlJobEventOutboxDB
from agent.services.source_control_job_events import SourceControlJobEvent
from agent.services.source_control_runtime_support import (
    SourceControlApiRuntimeError,
    iso_timestamp,
)


class SQLSourceControlJobEventRepository:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def read_after(
        self,
        *,
        tenant_id: str,
        project_id: str,
        after_sequence: int,
        limit: int,
    ) -> Sequence[SourceControlJobEvent]:
        with Session(self._engine) as db:
            rows = list(
                db.exec(
                    select(SourceControlJobEventOutboxDB)
                    .where(
                        SourceControlJobEventOutboxDB.tenant_id == tenant_id,
                        SourceControlJobEventOutboxDB.project_id == project_id,
                        SourceControlJobEventOutboxDB.sequence
                        > after_sequence,
                    )
                    .order_by(SourceControlJobEventOutboxDB.sequence)
                    .limit(limit)
                ).all()
            )
        return tuple(
                SourceControlJobEvent(
                    event_id=row.event_id,
                    sequence=int(row.sequence or 0),
                    tenant_id=row.tenant_id,
                    project_id=row.project_id,
                    resource_id=row.resource_id,
                    job_id=row.job_id,
                    event_type=row.event_type,
                    status=row.status,
                    reason_code=row.reason_code,
                    trace_id=row.trace_id,
                    occurred_at=iso_timestamp(row.occurred_at_epoch) or "",
                )
                for row in rows
            )

    def append(
        self,
        *,
        event_id: str,
        tenant_id: str,
        project_id: str,
        resource_id: str,
        job_id: str,
        event_type: str,
        status: str,
        reason_code: str | None,
        trace_id: str,
        occurred_at_epoch: float,
    ) -> SourceControlJobEvent:
        with Session(self._engine) as db:
            row = SourceControlJobEventOutboxDB(
                event_id=event_id,
                tenant_id=tenant_id,
                project_id=project_id,
                resource_id=resource_id,
                job_id=job_id,
                event_type=event_type,
                status=status,
                reason_code=reason_code,
                trace_id=trace_id,
                occurred_at_epoch=float(occurred_at_epoch),
                created_at_epoch=time.time(),
            )
            db.add(row)
            try:
                db.commit()
                db.refresh(row)
            except IntegrityError:
                db.rollback()
                row = db.exec(
                    select(SourceControlJobEventOutboxDB).where(
                        SourceControlJobEventOutboxDB.event_id == event_id
                    )
                ).first()
                if row is None or (
                    row.tenant_id,
                    row.project_id,
                    row.resource_id,
                    row.job_id,
                    row.event_type,
                    row.status,
                    row.reason_code,
                    row.trace_id,
                ) != (
                    tenant_id,
                    project_id,
                    resource_id,
                    job_id,
                    event_type,
                    status,
                    reason_code,
                    trace_id,
                ):
                    raise SourceControlApiRuntimeError(
                        "job_event_id_conflict", status_code=409
                    ) from None
            return SourceControlJobEvent(
                event_id=row.event_id,
                sequence=int(row.sequence or 0),
                tenant_id=row.tenant_id,
                project_id=row.project_id,
                resource_id=row.resource_id,
                job_id=row.job_id,
                event_type=row.event_type,
                status=row.status,
                reason_code=row.reason_code,
                trace_id=row.trace_id,
                occurred_at=iso_timestamp(row.occurred_at_epoch) or "",
            )


__all__ = ["SQLSourceControlJobEventRepository"]
