"""Fair, live-pressure-aware Hub scheduler for offline reconciliation."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterable, Mapping, Protocol

from agent.services.speech_reconciliation_task_port import (
    SpeechReconciliationTaskPort,
    SpeechReconciliationTaskReference,
)
from ananta_contracts.speech_reconciliation import SpeechReconciliationJob, SpeechResourceVector


class SpeechReconciliationSchedulingError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class SpeechReconciliationWorkerCandidate:
    worker_id: str
    location: str
    capabilities: frozenset[str]
    capacity: SpeechResourceVector
    max_offline_assignments: int
    active_offline_assignments: int
    available: bool = True
    draining: bool = False


@dataclass(frozen=True, slots=True)
class QueuedSpeechReconciliation:
    job: SpeechReconciliationJob
    tenant_id: str
    owner_subject: str
    priority: int
    queued_sequence: int
    allowed_locations: frozenset[str]
    requested_resources: SpeechResourceVector
    checkpoint_ref: str | None = None
    requested_compute_factor: int | None = None


@dataclass(frozen=True, slots=True)
class SpeechReconciliationLease:
    lease_id: str
    job: SpeechReconciliationJob
    worker_id: str
    expires_at_ms: int


class SpeechReconciliationLeasePort(Protocol):
    def acquire(
        self,
        queued: QueuedSpeechReconciliation,
        candidate: SpeechReconciliationWorkerCandidate,
        *,
        ttl_ms: int,
    ) -> SpeechReconciliationLease: ...

    def revoke(self, lease_id: str, *, reason_code: str) -> None: ...


@dataclass(frozen=True, slots=True)
class ScheduledSpeechReconciliation:
    lease: SpeechReconciliationLease
    task: SpeechReconciliationTaskReference


class SpeechReconciliationDispatchPort(Protocol):
    def dispatch(self, scheduled: ScheduledSpeechReconciliation) -> object: ...


class SpeechReconciliationScheduler:
    """Selects bounded work; repository/queue mutation stays behind Hub ports."""

    def __init__(
        self,
        *,
        leases: SpeechReconciliationLeasePort,
        tasks: SpeechReconciliationTaskPort,
        dispatcher: SpeechReconciliationDispatchPort | None = None,
        max_offline_assignments: int = 4,
        lease_ttl_ms: int = 30_000,
    ) -> None:
        if not 1 <= max_offline_assignments <= 128:
            raise ValueError("speech_reconciliation_capacity_invalid")
        if not 5_000 <= lease_ttl_ms <= 300_000:
            raise ValueError("speech_reconciliation_lease_ttl_invalid")
        self._leases = leases
        self._tasks = tasks
        self._dispatcher = dispatcher
        self._capacity = max_offline_assignments
        self._ttl_ms = lease_ttl_ms

    def schedule(
        self,
        queued: Iterable[QueuedSpeechReconciliation],
        candidates: Iterable[SpeechReconciliationWorkerCandidate],
        *,
        live_pressure: bool,
        tenant_active_assignments: Mapping[str, int] | None = None,
    ) -> tuple[ScheduledSpeechReconciliation, ...]:
        if live_pressure:
            return ()
        remaining = list(queued)
        workers = {candidate.worker_id: candidate for candidate in candidates}
        active_by_tenant = {str(key): max(0, int(value)) for key, value in (tenant_active_assignments or {}).items()}
        scheduled: list[ScheduledSpeechReconciliation] = []
        while remaining and len(scheduled) < self._capacity:
            remaining.sort(
                key=lambda item: (
                    active_by_tenant.get(item.tenant_id, 0),
                    -self._priority(item.priority),
                    item.queued_sequence,
                    item.job.job_id,
                )
            )
            selected_index = next(
                (index for index, item in enumerate(remaining) if self._eligible_workers(item, workers.values())),
                None,
            )
            if selected_index is None:
                break
            item = remaining.pop(selected_index)
            eligible = self._eligible_workers(item, workers.values())
            worker = sorted(
                eligible,
                key=lambda candidate: (
                    candidate.active_offline_assignments,
                    candidate.worker_id,
                ),
            )[0]
            lease = self._leases.acquire(item, worker, ttl_ms=self._ttl_ms)
            try:
                task = self._tasks.enqueue_attempt(
                    lease.job,
                    tenant_id=item.tenant_id,
                    owner_subject=item.owner_subject,
                    worker_id=worker.worker_id,
                    worker_location=worker.location,
                    resource_profile=item.requested_resources.to_dict(),
                    checkpoint_ref=item.checkpoint_ref,
                )
            except Exception:
                self._leases.revoke(lease.lease_id, reason_code="speech_reconciliation_task_projection_failed")
                raise
            scheduled_item = ScheduledSpeechReconciliation(lease, task)
            if self._dispatcher is not None:
                try:
                    self._dispatcher.dispatch(scheduled_item)
                except Exception:
                    if task.attempt_task_id is not None:
                        try:
                            self._tasks.cancel(
                                task.attempt_task_id,
                                reason_code="speech_reconciliation_dispatch_failed",
                            )
                        except Exception:
                            pass
                    self._leases.revoke(
                        lease.lease_id,
                        reason_code="speech_reconciliation_dispatch_failed",
                    )
                    raise
            scheduled.append(scheduled_item)
            active_by_tenant[item.tenant_id] = active_by_tenant.get(item.tenant_id, 0) + 1
            workers[worker.worker_id] = replace(
                worker,
                active_offline_assignments=worker.active_offline_assignments + 1,
            )
        return tuple(scheduled)

    @staticmethod
    def _priority(value: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100:
            raise SpeechReconciliationSchedulingError("speech_reconciliation_priority_invalid")
        return value

    @staticmethod
    def _eligible_workers(
        item: QueuedSpeechReconciliation,
        candidates: Iterable[SpeechReconciliationWorkerCandidate],
    ) -> list[SpeechReconciliationWorkerCandidate]:
        return [
            candidate
            for candidate in candidates
            if candidate.available
            and not candidate.draining
            and "speech_reconciliation" in candidate.capabilities
            and candidate.location in item.allowed_locations
            and candidate.active_offline_assignments < candidate.max_offline_assignments
            and candidate.capacity.covers(item.requested_resources)
        ]


__all__ = [
    "QueuedSpeechReconciliation",
    "ScheduledSpeechReconciliation",
    "SpeechReconciliationLease",
    "SpeechReconciliationLeasePort",
    "SpeechReconciliationDispatchPort",
    "SpeechReconciliationScheduler",
    "SpeechReconciliationSchedulingError",
    "SpeechReconciliationWorkerCandidate",
]
