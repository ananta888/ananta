"""Read authoritative task/job/slot bindings for a scoped inference grant."""

from __future__ import annotations

import math
import time

from sqlmodel import Session

from agent.db_models.tasks import TaskDB
from agent.db_models.workers import WorkerJobDB, WorkerSlotLeaseDB


def _engine():
    from agent.database import engine

    return engine


class SqlSpecialistDispatchLeaseAuthority:
    def __init__(self, *, tenant_id, project_id, engine=_engine, clock=time.time):
        self._tenant, self._project, self._engine, self._clock = tenant_id, project_id, engine, clock

    def require_current(self, *, task_id, assignment_id, dispatch_lease_id):
        with Session(self._engine()) as session:
            task = session.get(TaskDB, task_id)
            job = session.get(WorkerJobDB, assignment_id)
            lease = session.get(WorkerSlotLeaseDB, dispatch_lease_id)
            if (
                task is None
                or job is None
                or lease is None
                or (task.tenant_id, task.project_id) != (self._tenant, self._project)
                or task.status not in {"running", "proposing", "executing", "reserved", "in_progress"}
                or task.current_worker_job_id != assignment_id
                or task_id not in {job.parent_task_id, job.subtask_id}
                or job.status not in {"delegated", "running", "forwarded", "dispatched"}
                or job.slot_lease_id != dispatch_lease_id
                or lease.worker_job_id != assignment_id
                or lease.parent_task_id not in {task_id, job.parent_task_id}
                or not job.selected_worker_id
                or lease.worker_id != job.selected_worker_id
                or lease.status != "active"
                or lease.released_at is not None
                or not math.isfinite(lease.deadline_at)
                or not self._clock() < lease.deadline_at
            ):
                raise ValueError("specialist_dispatch_lease_not_current")
            return lease.deadline_at
