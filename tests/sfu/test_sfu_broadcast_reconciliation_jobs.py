from __future__ import annotations

import time

import pytest

from agent.services.sfu_broadcast_background_job_port import (
    SfuBroadcastBackgroundJobLease,
)
from agent.services.sfu_broadcast_reconciler_scheduler import SfuBroadcastJobContext
from agent.services.sfu_broadcast_reconciliation_jobs import (
    SfuFleetReconciliationScheduledJob,
    SfuRouteReconciliationScopeCandidate,
    SfuRouteReconciliationScopePage,
)
from agent.services.sfu_fleet_reconciliation_service import (
    SfuFleetReconciliationPage,
)
from agent.services.sfu_fanout_reconciliation_service import RouteReconciliationScope


class _EmptyState:
    def __init__(self) -> None:
        self.partitions: list[str] = []

    def scan(self, *, partition, cursor, limit, now_ms):
        self.partitions.append(partition)
        return SfuFleetReconciliationPage((), None)


class _NoMutation:
    def fence_route(self, **kwargs):
        raise AssertionError("empty state must not mutate")

    release_reservation = fence_route
    mark_node_unknown = fence_route
    reconcile_desired_route = fence_route


class _ScopePage:
    def page(self, *, cursor, limit):
        return SfuRouteReconciliationScopePage(
            (
                SfuRouteReconciliationScopeCandidate(
                    RouteReconciliationScope("tenant-a", "room-a"),
                    "scope-cursor",
                ),
            ),
            None,
        )


def _context(valid) -> SfuBroadcastJobContext:
    lease = SfuBroadcastBackgroundJobLease(
        job_id="job-1",
        name="fleet_reconciliation",
        partition_key="global",
        owner_id="hub-a",
        fencing_token=7,
        version=3,
        lease_expires_at=time.time() + 60,
        resume_cursor=None,
        batch_size_max=10,
        runtime_deadline_ms=2_000,
    )
    return SfuBroadcastJobContext(lease=lease, _lease_valid=valid)


def test_fleet_job_reuses_durable_scheduler_lease_and_partition() -> None:
    state = _EmptyState()
    job = SfuFleetReconciliationScheduledJob(
        state=state,
        mutations=_NoMutation(),
    )

    assert job.run(_context(lambda: True)) is None
    assert state.partitions == ["global"]


def test_fleet_job_stops_before_state_access_after_scheduler_takeover() -> None:
    state = _EmptyState()
    job = SfuFleetReconciliationScheduledJob(
        state=state,
        mutations=_NoMutation(),
    )

    with pytest.raises(RuntimeError, match="sfu_background_job_lease_lost"):
        job.run(_context(lambda: False))

    assert state.partitions == []


def test_route_scope_page_is_bounded_and_cursor_bearing() -> None:
    page = _ScopePage().page(cursor=None, limit=1)

    assert page.items[0].scope == RouteReconciliationScope("tenant-a", "room-a")
    assert page.items[0].cursor_after == "scope-cursor"
