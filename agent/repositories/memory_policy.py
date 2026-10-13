"""Resolve current Hub policy against actual SQL task and dispatch bindings."""

import time

from sqlmodel import Session

from agent.db_models import MemoryRunPolicyDB, TaskDB, WorkerJobDB, WorkerSlotLeaseDB
from agent.services.memory_policy_codec import decode_policy, encode_policy
from agent.services.task_status_service import TERMINAL_TASK_STATUSES, normalize_task_status
from ananta_contracts.memory_access import MemoryActor, MemoryRunPolicy


class SQLMemoryRunPolicyRepository:
    def __init__(self, engine, *, clock=time.time, hub_read_authority=None):
        self._engine = engine
        self._clock = clock
        self._hub_read_authority = hub_read_authority

    def reserve(self, policy: MemoryRunPolicy, *, worker_job_id=None, dispatch_lease_id=None):
        """Internal Hub admission only. No HTTP/Worker caller may supply this DTO."""
        if not isinstance(policy, MemoryRunPolicy) or not policy.active:
            raise ValueError("memory_policy_admission_invalid")
        if bool(worker_job_id) != bool(dispatch_lease_id):
            raise ValueError("memory_dispatch_binding_required")
        row = MemoryRunPolicyDB(
            run_id=policy.namespace.run_id, tenant_id=policy.actor.tenant_id,
            actor_subject=policy.actor.subject, task_id=policy.namespace.task_id,
            policy=encode_policy(policy), policy_digest=policy.digest, expires_at=policy.expires_at,
            worker_job_id=worker_job_id, dispatch_lease_id=dispatch_lease_id,
        )
        with Session(self._engine) as session:
            if not self._bindings_match(session, row, policy):
                raise ValueError("memory_policy_task_binding_mismatch")
            session.add(row)
            session.commit()

    def current(self, *, actor: MemoryActor, run_id: str) -> MemoryRunPolicy | None:
        with Session(self._engine) as session:
            row = session.get(MemoryRunPolicyDB, run_id)
            if (row is None or row.revoked or row.expires_at <= self._clock()
                    or (row.tenant_id, row.actor_subject) != (actor.tenant_id, actor.subject)):
                return None
            policy = decode_policy(row.policy)
            if (policy.digest != row.policy_digest or policy.actor != actor
                    or policy.namespace.run_id != run_id or policy.expires_at != row.expires_at
                    or not self._bindings_match(session, row, policy)):
                return None
            if row.worker_job_id is None and self._hub_read_authority is not None:
                task = session.get(TaskDB, row.task_id)
                if self._hub_read_authority(policy, task.model_dump()) is not True:
                    return None
            return policy

    def _bindings_match(self, session, row, policy):
        task = session.get(TaskDB, row.task_id)
        namespace = policy.namespace
        if task is None or (task.id, task.tenant_id, task.project_id, task.goal_id) != (
            namespace.task_id, namespace.tenant_id, namespace.workspace_id, namespace.goal_id,
        ):
            return False
        if row.worker_job_id is None and row.dispatch_lease_id is None:
            # Bounded Hub read/admin session, distinct from a Worker execution grant.
            return True
        job = session.get(WorkerJobDB, row.worker_job_id)
        lease = session.get(WorkerSlotLeaseDB, row.dispatch_lease_id)
        return bool(
            job and lease and (job.subtask_id or job.parent_task_id) == task.id
            and task.current_worker_job_id == job.id
            and job.slot_lease_id == lease.id and lease.worker_job_id == job.id
            and lease.parent_task_id == job.parent_task_id and lease.status == "active" and lease.released_at is None
            and self._clock() < lease.deadline_at and policy.expires_at <= lease.deadline_at
            and job.status in {"created", "queued", "scheduled", "running", "dispatched", "delegated"}
            and lease.worker_id == job.selected_worker_id and lease.worker_id == policy.actor.subject
            and normalize_task_status(task.status) not in TERMINAL_TASK_STATUSES
        )

    def revoke(self, *, actor: MemoryActor, run_id: str):
        with Session(self._engine) as session:
            row = session.get(MemoryRunPolicyDB, run_id)
            if row is not None and (row.tenant_id, row.actor_subject) == (actor.tenant_id, actor.subject):
                row.revoked = True
                session.add(row)
                session.commit()
