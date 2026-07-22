from dataclasses import replace

from agent.services.sfu_fleet_reconciliation_service import (
    SfuFleetReconciliationItem,
    SfuFleetReconciliationLease,
    SfuFleetReconciliationPage,
    SfuFleetReconciliationPolicy,
    SfuFleetReconciliationService,
)


NOW = 1_000_000


class Leases:
    def __init__(self):
        self.current = True
        self.checkpoint_cursor = None

    def acquire(self, *, scope, owner_id, ttl_ms, now_ms):
        if not self.current:
            return None
        return SfuFleetReconciliationLease(scope, owner_id, 17, self.checkpoint_cursor, now_ms + ttl_ms, 1)

    def renew(self, lease, *, ttl_ms, now_ms):
        return replace(lease, expires_at_ms=now_ms + ttl_ms, version=lease.version + 1)

    def is_current(self, lease, *, now_ms):
        return self.current and now_ms < lease.expires_at_ms

    def checkpoint(self, lease, *, cursor, now_ms):
        self.checkpoint_cursor = cursor
        return replace(lease, checkpoint_cursor=cursor, version=lease.version + 1)

    def release(self, lease, *, now_ms):
        return None


class State:
    def __init__(self, item=None, failure=False):
        self.item = item
        self.failure = failure

    def scan(self, *, partition, cursor, limit, now_ms):
        if self.failure:
            raise ConnectionError("content must not escape")
        if cursor is not None or self.item is None:
            return SfuFleetReconciliationPage((), None)
        return SfuFleetReconciliationPage((self.item,), None)


class Mutations:
    def __init__(self):
        self.actions = []

    def fence_route(self, **kwargs):
        self.actions.append(("fence", kwargs["fencing_token"]))
        return True

    def release_reservation(self, **kwargs):
        self.actions.append(("release", kwargs["fencing_token"]))
        return True

    def mark_node_unknown(self, **kwargs):
        self.actions.append(("unknown", kwargs["fencing_token"]))
        return True

    def reconcile_desired_route(self, **kwargs):
        self.actions.append(("activate", kwargs["fencing_token"]))
        return True


def stale_item():
    return SfuFleetReconciliationItem(
        item_id="room-a",
        cursor_after="cursor-1",
        expected_state_version=3,
        desired_route=False,
        desired_route_version=4,
        active_route=True,
        active_route_version=3,
        route_intent_expires_at_ms=NOW - 1,
        reservation_active=True,
        reservation_orphaned=True,
        reservation_expires_at_ms=NOW - 1,
        observation_fresh_until_ms=NOW - 1,
        stale_access_expires_at_ms=NOW - 1,
        node_health="healthy",
        admission_ready=True,
        control_plane_consistent=True,
    )


def service(leases, state, mutations):
    return SfuFleetReconciliationService(
        leases=leases,
        state=state,
        mutations=mutations,
        policy=SfuFleetReconciliationPolicy(runtime_ms_max=100, lease_ttl_ms=500),
        wall_clock_ms=lambda: NOW,
        monotonic_ms=lambda: 10,
        sleeper=lambda seconds: None,
    )


def test_stale_authority_is_fenced_before_release_and_health_demotion():
    mutations = Mutations()
    result = service(Leases(), State(stale_item()), mutations).run_once(
        partition="tenant-a", owner_id="hub-a"
    )

    assert result.status == "completed"
    assert mutations.actions == [("fence", 17), ("release", 17), ("unknown", 17)]


def test_db_partition_never_activates_or_renews_routes():
    mutations = Mutations()
    result = service(Leases(), State(failure=True), mutations).run_once(
        partition="tenant-a", owner_id="hub-a"
    )

    assert result.status == "stopped"
    assert result.reason_code == "sfu_reconcile_state_unavailable"
    assert mutations.actions == []


def test_second_hub_without_lease_does_no_work():
    leases = Leases()
    leases.current = False
    mutations = Mutations()

    result = service(leases, State(stale_item()), mutations).run_once(
        partition="tenant-a", owner_id="hub-b"
    )

    assert result.status == "skipped"
    assert mutations.actions == []

