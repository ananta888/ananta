from __future__ import annotations

from dataclasses import replace

from agent.repositories.sfu_broadcast_background_job_repository import (
    SfuBroadcastBackgroundJobLease,
    SfuBroadcastBackgroundJobSpec,
)
from agent.services.sfu_broadcast_reconciler_scheduler import CallableSfuBroadcastJob, SfuBroadcastReconcilerScheduler


class LeaseRepository:
    def __init__(self): self.owner = None; self.finished = []
    def claim(self, spec, *, owner_id, now):
        if self.owner not in (None, owner_id): return None
        self.owner = owner_id
        return SfuBroadcastBackgroundJobLease("job", spec.name, spec.partition_key, owner_id, 1, 2, now + 10, None, 5, 1000)
    def lease_valid(self, lease, *, now): return self.owner == lease.owner_id and now < lease.lease_expires_at
    def finish(self, lease, *, status, reason_code, resume_cursor, now): self.finished.append((status, reason_code)); self.owner = None
    def release_owner(self, owner_id, *, now): self.owner = None; return 1


def test_two_hubs_cannot_claim_the_same_partition():
    repository = LeaseRepository()
    spec = SfuBroadcastBackgroundJobSpec("route", enabled=True, interval_ms_min=100)
    job = CallableSfuBroadcastJob(lambda context: "next")
    first = SfuBroadcastReconcilerScheduler(repository, {"route": job}, (spec,), owner_id="hub-a", clock=lambda: 1.0)
    second = SfuBroadcastReconcilerScheduler(repository, {"route": job}, (spec,), owner_id="hub-b", clock=lambda: 1.0)
    lease = repository.claim(spec, owner_id="hub-a", now=1.0)
    assert lease is not None
    assert repository.claim(spec, owner_id="hub-b", now=1.0) is None
    first.stop()
    second.stop()
