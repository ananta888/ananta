from __future__ import annotations

from agent.adapters.sfu_broadcast_mock_adapter import (
    DeterministicSfuBroadcastRouteMockAdapter,
    ScriptedRouteFaultPlanV1,
)
from agent.services.sfu_broadcast_route_port import (
    ApplyRouteCommandV1,
    MediaKindV1,
    RouteKeyV1,
    RouteLayerV1,
    RoutePresenceV1,
    RouteProjectionV1,
    RouteReasonCodeV1,
    RouteTrafficBudgetV1,
    RouteVersionV1,
    RuntimeControlModeV1,
)
from agent.services.sfu_fanout_reconciliation_service import (
    ReconciliationAction,
    ReconciliationDesiredState,
    ReconciliationPhase,
    ReconciliationRunStatus,
    RouteReconciliationAuthority,
    RouteReconciliationCandidate,
    RouteReconciliationCursor,
    RouteReconciliationLease,
    RouteReconciliationPage,
    RouteReconciliationScope,
    SfuFanoutReconciliationConfig,
    SfuFanoutRouteReconciliationService,
)


NOW_MS = 1_900_000_000_000
SCOPE = RouteReconciliationScope("tenant-a", "room-a")


class Clock:
    value = NOW_MS

    def now_ms(self):
        return self.value


class LeasePort:
    def __init__(self, available=True):
        self.available = available
        self.released = []

    def acquire(self, *, scope, owner_ref, now_ms, lease_ttl_ms):
        if not self.available:
            return None
        return RouteReconciliationLease(
            scope, owner_ref, "lease-fence", now_ms + lease_ttl_ms
        )

    def release(self, lease):
        self.released.append(lease)


class Pages:
    def __init__(self, by_phase, *, fail=False):
        self.by_phase = by_phase
        self.fail = fail
        self.calls = []

    def page(self, *, phase, cursor, page_size, **kwargs):
        del kwargs
        if self.fail:
            raise RuntimeError("database unavailable")
        self.calls.append((phase, cursor, page_size))
        items = self.by_phase.get((phase, cursor), ())[:page_size]
        next_cursor = items[-1].resume_cursor if items else None
        return RouteReconciliationPage(tuple(items), next_cursor)


class Authorities:
    def __init__(self, values):
        self.values = values

    def resolve(self, *, candidate, observation, lease, now_ms):
        value = self.values[candidate.candidate_ref]
        if callable(value):
            return value(candidate, observation, lease, now_ms)
        return value


class Checkpoints:
    def __init__(self):
        self.values = []

    def save(self, *, lease, cursor):
        del lease
        self.values.append(cursor)


class Outcomes:
    def __init__(self):
        self.values = []

    def record(self, *, lease, outcome):
        del lease
        self.values.append(outcome)


class RecordingRoutes:
    def __init__(self, delegate):
        self.delegate = delegate
        self.calls = []

    def apply(self, command):
        self.calls.append(("apply", command.desired.key.route_id))
        return self.delegate.apply(command)

    def update(self, command):
        self.calls.append(("update", command.desired.key.route_id))
        return self.delegate.update(command)

    def revoke(self, command):
        self.calls.append(("revoke", command.key.route_id))
        return self.delegate.revoke(command)

    def observe(self, query):
        return self.delegate.observe(query)


def version(number):
    return RouteVersionV1(number, number, number, number, f"fence-{number}")


def projection(route_id, number=1):
    return RouteProjectionV1(
        RouteKeyV1("tenant-a", "room-a", route_id),
        f"group-{route_id}",
        number,
        f"{number:064x}",
        f"snapshot-{number}",
        number,
        ("A" * 42) + str(number),
        ("A" * 22,),
        RuntimeControlModeV1.LIVEKIT_CONTROL_API,
        "cluster-a",
        "region-a",
        None,
        f"publication-{route_id}",
        MediaKindV1.VIDEO,
        (RouteLayerV1("low", 0, 0, "q"),),
        (RouteTrafficBudgetV1("media", 1_000_000, 2_000, 64_000),),
        2_000_000,
        NOW_MS - 100,
        NOW_MS + 5_000,
        version(number),
        f"{number + 100:064x}",
    )


def candidate(route_id, phase, cursor):
    return RouteReconciliationCandidate(
        f"candidate-{route_id}", projection(route_id).key, phase, cursor
    )


def authority(item, *, state, desired=None, expected=None, revoke=None):
    return RouteReconciliationAuthority(
        item.candidate_ref,
        item.key,
        state,
        desired,
        expected,
        revoke,
        f"operation-{item.candidate_ref}",
        "lease-fence",
        True,
        True,
        True,
        True,
        "authorized",
    )


def service(pages, authorities, *, config=None, leases=None):
    clock = Clock()
    mock = DeterministicSfuBroadcastRouteMockAdapter(
        clock=clock, fault_plan=ScriptedRouteFaultPlanV1()
    )
    routes = RecordingRoutes(mock)
    outcomes = Outcomes()
    checkpoints = Checkpoints()
    svc = SfuFanoutRouteReconciliationService(
        config=config or SfuFanoutReconciliationConfig(),
        clock=clock,
        leases=leases or LeasePort(),
        pages=pages,
        authority=Authorities(authorities),
        checkpoints=checkpoints,
        outcomes=outcomes,
        apply_routes=routes,
        update_routes=routes,
        revoke_routes=routes,
        observe_routes=routes,
    )
    return svc, routes, outcomes, checkpoints, mock


def test_revocations_converge_before_missing_current_routes_are_applied():
    stale = candidate("stale", ReconciliationPhase.REVOKE, "revoked-1")
    missing = candidate("missing", ReconciliationPhase.ENSURE, "ensured-1")
    pages = Pages(
        {
            (ReconciliationPhase.REVOKE, None): (stale,),
            (ReconciliationPhase.REVOKE, "revoked-1"): (),
            (ReconciliationPhase.ENSURE, None): (missing,),
            (ReconciliationPhase.ENSURE, "ensured-1"): (),
        }
    )
    svc, routes, outcomes, _checkpoints, mock = service(
        pages,
        {
            stale.candidate_ref: authority(
                stale,
                state=ReconciliationDesiredState.REVOKED,
                expected=version(1),
                revoke=version(2),
            ),
            missing.candidate_ref: authority(
                missing,
                state=ReconciliationDesiredState.ACTIVE,
                desired=projection("missing"),
            ),
        },
    )
    mock.apply(ApplyRouteCommandV1("setup-stale", projection("stale")))
    result = svc.reconcile(scope=SCOPE, owner_ref="hub-a")
    assert result.status is ReconciliationRunStatus.COMPLETED
    assert routes.calls == [("revoke", "stale"), ("apply", "missing")]
    assert [item.action for item in outcomes.values] == [
        ReconciliationAction.REVOKED,
        ReconciliationAction.APPLIED,
    ]


def test_item_bound_returns_resume_cursor_and_second_hub_is_lease_denied():
    first = candidate("one", ReconciliationPhase.REVOKE, "after-one")
    pages = Pages({(ReconciliationPhase.REVOKE, None): (first,)})
    config = SfuFanoutReconciliationConfig(reconcile_items_max=1)
    svc, _routes, _outcomes, _checkpoints, _mock = service(
        pages,
        {
            first.candidate_ref: authority(
                first, state=ReconciliationDesiredState.TOMBSTONED
            )
        },
        config=config,
    )
    result = svc.reconcile(scope=SCOPE, owner_ref="hub-a")
    assert result.status is ReconciliationRunStatus.PARTIAL
    assert result.next_cursor == RouteReconciliationCursor(
        ReconciliationPhase.REVOKE, "after-one"
    )
    busy, *_ = service(pages, {}, leases=LeasePort(available=False))
    denied = busy.reconcile(scope=SCOPE, owner_ref="hub-b")
    assert denied.status is ReconciliationRunStatus.BUSY


def test_database_failure_is_bounded_and_revocation_wins_during_recovery():
    failing, *_ = service(Pages({}, fail=True), {})
    failed = failing.reconcile(scope=SCOPE, owner_ref="hub-a")
    assert failed.status is ReconciliationRunStatus.FAILED
    assert failed.pages_read == 0

    recovering = candidate("recovery", ReconciliationPhase.ENSURE, "after-recovery")
    pages = Pages(
        {
            (ReconciliationPhase.REVOKE, None): (),
            (ReconciliationPhase.ENSURE, None): (recovering,),
            (ReconciliationPhase.ENSURE, "after-recovery"): (),
        }
    )
    svc, routes, outcomes, _checkpoints, mock = service(
        pages,
        {
            recovering.candidate_ref: authority(
                recovering,
                state=ReconciliationDesiredState.REVOKED,
                expected=version(1),
                revoke=version(2),
            )
        },
    )
    mock.apply(ApplyRouteCommandV1("setup-recovery", projection("recovery")))
    result = svc.reconcile(scope=SCOPE, owner_ref="hub-a")
    assert result.status is ReconciliationRunStatus.COMPLETED
    assert routes.calls == [("revoke", "recovery")]
    assert outcomes.values[0].action is ReconciliationAction.REVOKED


def test_unknown_runtime_observation_never_blindly_applies():
    ensure = candidate("unknown", ReconciliationPhase.ENSURE, "after-unknown")
    pages = Pages(
        {
            (ReconciliationPhase.REVOKE, None): (),
            (ReconciliationPhase.ENSURE, None): (ensure,),
            (ReconciliationPhase.ENSURE, "after-unknown"): (),
        }
    )
    svc, routes, outcomes, _checkpoints, mock = service(
        pages,
        {
            ensure.candidate_ref: authority(
                ensure,
                state=ReconciliationDesiredState.ACTIVE,
                desired=projection("unknown"),
            )
        },
    )
    original_observe = mock.observe

    def unknown(query):
        observed = original_observe(query)
        return type(observed)(
            observed.key,
            RoutePresenceV1.UNKNOWN,
            RouteReasonCodeV1.OBSERVATION_UNSUPPORTED,
            None,
            None,
            NOW_MS,
            False,
        )

    routes.observe = unknown
    result = svc.reconcile(scope=SCOPE, owner_ref="hub-a")
    assert result.deferred == 1
    assert routes.calls == []
    assert outcomes.values[0].action is ReconciliationAction.DEFERRED
