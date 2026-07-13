"""SQL-backed global capacity reservations for Hub runtime delegation."""

from __future__ import annotations

import os
import time
from collections.abc import Mapping
from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from agent.db_models.workflow_runtime import (
    WorkflowRuntimeCapacityLockDB,
    WorkflowRuntimeCapacityReservationDB,
)
from agent.services.workflow_adapter_task_queue_service import (
    WorkflowAdapterQueueError,
    WorkflowAdapterTaskQueuePort,
    WorkflowAdapterTaskReceipt,
    WorkflowAdapterTaskSubmission,
)
from agent.services.workflow_runtime.sqlalchemy_support import (
    SessionFactory,
    SQLAlchemyStoreSupport,
    stable_row_id,
)

_TERMINAL = frozenset({"completed", "failed", "cancelled"})


class SQLAlchemyWorkflowRuntimeCapacity(SQLAlchemyStoreSupport):
    """Serialize tenant/global slot claims through one durable lock row."""

    def __init__(
        self,
        bind: Engine | SessionFactory,
        *,
        tenant_limit: int | None = None,
        worker_limit: int | None = None,
        clock=time.time,
    ) -> None:
        super().__init__(bind)
        self._tenant_limit = _limit(
            tenant_limit,
            env="ANANTA_LANGGRAPH_GLOBAL_TENANT_PARALLEL_LIMIT",
            default=64,
        )
        self._worker_limit = _limit(
            worker_limit,
            env="ANANTA_LANGGRAPH_GLOBAL_WORKER_PARALLEL_LIMIT",
            default=256,
        )
        self._clock = clock
        self._ensure_lock()

    def available_slots(
        self,
        *,
        tenant_id: str,
        workflow_id: str,
        run_id: str,
    ) -> int:
        del workflow_id  # the run is the accounting unit; workflow ids can repeat
        with self._read_session() as session:
            base = sa.select(sa.func.count()).select_from(
                WorkflowRuntimeCapacityReservationDB
            ).where(
                WorkflowRuntimeCapacityReservationDB.active.is_(True),
                sa.not_(
                    sa.and_(
                        WorkflowRuntimeCapacityReservationDB.tenant_id == tenant_id,
                        WorkflowRuntimeCapacityReservationDB.run_id == run_id,
                    )
                ),
            )
            global_active = int(session.execute(base).scalar_one())
            tenant_active = int(
                session.execute(
                    base.where(
                        WorkflowRuntimeCapacityReservationDB.tenant_id == tenant_id,
                    )
                ).scalar_one()
            )
        return max(
            0,
            min(
                self._worker_limit - global_active,
                self._tenant_limit - tenant_active,
            ),
        )

    def reserve(
        self,
        submission: WorkflowAdapterTaskSubmission,
        *,
        tenant_limit: int,
        worker_limit: int,
    ) -> str:
        reservation_id = stable_row_id(
            "wrcap",
            submission.tenant_id,
            submission.run_id,
            submission.step_id,
            submission.idempotency_key,
        )
        try:
            with self._transaction() as session:
                locked = session.execute(
                    sa.update(WorkflowRuntimeCapacityLockDB)
                    .where(WorkflowRuntimeCapacityLockDB.id == "global")
                    .values(
                        revision=WorkflowRuntimeCapacityLockDB.revision + 1,
                        updated_at=float(self._clock()),
                    )
                )
                if int(locked.rowcount or 0) != 1:
                    raise WorkflowAdapterQueueError(
                        "langgraph_global_capacity_lock_unavailable",
                        status_code=503,
                    )
                existing = session.get(
                    WorkflowRuntimeCapacityReservationDB,
                    reservation_id,
                )
                if existing is not None:
                    _assert_binding(existing, submission)
                    return reservation_id
                global_active = _active_count(session)
                tenant_active = _active_count(
                    session,
                    tenant_id=submission.tenant_id,
                )
                effective_tenant = min(self._tenant_limit, _positive(tenant_limit))
                effective_worker = min(self._worker_limit, _positive(worker_limit))
                if tenant_active >= effective_tenant:
                    raise WorkflowAdapterQueueError(
                        "langgraph_global_tenant_capacity_exhausted",
                        status_code=429,
                    )
                if global_active >= effective_worker:
                    raise WorkflowAdapterQueueError(
                        "langgraph_global_worker_capacity_exhausted",
                        status_code=429,
                    )
                session.add(
                    WorkflowRuntimeCapacityReservationDB(
                        id=reservation_id,
                        tenant_id=submission.tenant_id,
                        workflow_id=submission.workflow_id,
                        run_id=submission.run_id,
                        step_id=submission.step_id,
                        hub_task_id="",
                        active=True,
                        created_at=float(self._clock()),
                        released_at=0.0,
                    )
                )
        except IntegrityError as exc:
            raise WorkflowAdapterQueueError(
                "langgraph_global_capacity_concurrent_update",
                status_code=409,
            ) from exc
        return reservation_id

    def bind_task(self, reservation_id: str, *, hub_task_id: str) -> None:
        with self._transaction() as session:
            row = session.get(WorkflowRuntimeCapacityReservationDB, reservation_id)
            if row is None:
                raise WorkflowAdapterQueueError(
                    "langgraph_global_capacity_reservation_not_found",
                    status_code=409,
                )
            if row.hub_task_id and row.hub_task_id != hub_task_id:
                raise WorkflowAdapterQueueError(
                    "langgraph_global_capacity_task_binding_conflict",
                    status_code=409,
                )
            row.hub_task_id = str(hub_task_id)

    def release(self, reservation_id: str) -> None:
        with self._transaction() as session:
            row = session.get(WorkflowRuntimeCapacityReservationDB, reservation_id)
            if row is not None and row.active:
                row.active = False
                row.released_at = float(self._clock())

    def release_for_task(self, hub_task_id: str) -> None:
        with self._transaction() as session:
            rows = session.execute(
                sa.select(WorkflowRuntimeCapacityReservationDB).where(
                    WorkflowRuntimeCapacityReservationDB.hub_task_id == hub_task_id,
                    WorkflowRuntimeCapacityReservationDB.active.is_(True),
                )
            ).scalars()
            for row in rows:
                row.active = False
                row.released_at = float(self._clock())

    def _ensure_lock(self) -> None:
        try:
            with self._transaction() as session:
                if session.get(WorkflowRuntimeCapacityLockDB, "global") is None:
                    session.add(
                        WorkflowRuntimeCapacityLockDB(
                            id="global",
                            revision=0,
                            updated_at=float(self._clock()),
                        )
                    )
        except IntegrityError:
            pass


class CapacityGuardedWorkflowAdapterQueue:
    """Decorate task creation with an atomic global slot reservation."""

    def __init__(
        self,
        queue: WorkflowAdapterTaskQueuePort,
        capacity: SQLAlchemyWorkflowRuntimeCapacity,
    ) -> None:
        self._queue = queue
        self._capacity = capacity

    def submit(self, submission: WorkflowAdapterTaskSubmission) -> WorkflowAdapterTaskReceipt:
        limits = _parallel_limits(submission.payload)
        reservation = self._capacity.reserve(
            submission,
            tenant_limit=limits[0],
            worker_limit=limits[1],
        )
        try:
            receipt = self._queue.submit(submission)
        except Exception:
            self._capacity.release(reservation)
            raise
        self._capacity.bind_task(reservation, hub_task_id=receipt.hub_task_id)
        if str(receipt.status).lower() in _TERMINAL:
            self._capacity.release(reservation)
        return receipt

    def status(self, **scope: Any) -> dict[str, Any]:
        return self._release_terminal(self._queue.status(**scope))

    def inspect(self, **scope: Any) -> dict[str, Any]:
        return self._queue.inspect(**scope)

    def cancel(self, **scope: Any) -> dict[str, Any]:
        return self._release_terminal(self._queue.cancel(**scope))

    def history(self, **scope: Any) -> tuple[dict[str, Any], ...]:
        return self._queue.history(**scope)

    def _release_terminal(self, result: dict[str, Any]) -> dict[str, Any]:
        if str(result.get("status") or "").lower() in _TERMINAL:
            task_id = str(result.get("hub_task_id") or "")
            if task_id:
                self._capacity.release_for_task(task_id)
        return result


def _active_count(session: Any, *, tenant_id: str = "") -> int:
    statement = sa.select(sa.func.count()).select_from(
        WorkflowRuntimeCapacityReservationDB
    ).where(WorkflowRuntimeCapacityReservationDB.active.is_(True))
    if tenant_id:
        statement = statement.where(
            WorkflowRuntimeCapacityReservationDB.tenant_id == tenant_id
        )
    return int(session.execute(statement).scalar_one())


def _assert_binding(row: Any, submission: WorkflowAdapterTaskSubmission) -> None:
    if (
        row.tenant_id != submission.tenant_id
        or row.workflow_id != submission.workflow_id
        or row.run_id != submission.run_id
        or row.step_id != submission.step_id
    ):
        raise WorkflowAdapterQueueError(
            "langgraph_global_capacity_binding_conflict",
            status_code=409,
        )


def _parallel_limits(payload: Mapping[str, Any]) -> tuple[int, int]:
    raw = payload.get("parallel_limits")
    if not isinstance(raw, Mapping):
        raise WorkflowAdapterQueueError(
            "langgraph_parallel_limit_binding_required",
            status_code=422,
        )
    return _positive(raw.get("tenant")), _positive(raw.get("worker"))


def _limit(value: int | None, *, env: str, default: int) -> int:
    raw = value if value is not None else os.environ.get(env) or default
    return _positive(raw)


def _positive(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("langgraph_global_capacity_limit_invalid") from exc
    if parsed < 1 or parsed > 100_000:
        raise ValueError("langgraph_global_capacity_limit_invalid")
    return parsed


__all__ = [
    "CapacityGuardedWorkflowAdapterQueue",
    "SQLAlchemyWorkflowRuntimeCapacity",
]
