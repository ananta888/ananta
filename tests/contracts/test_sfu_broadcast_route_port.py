"""Reusable v1 route-port contract and deterministic mock fault coverage."""

from __future__ import annotations

from dataclasses import dataclass, replace

import pytest

from agent.adapters.sfu_broadcast_mock_adapter import (
    DeterministicSfuBroadcastRouteMockAdapter,
    RouteFaultV1,
    ScriptedRouteFaultPlanV1,
)
from agent.services.sfu_broadcast_route_port import (
    ApplyRouteCommandV1,
    ApplyRoutePortV1,
    MediaKindV1,
    ObserveRoutePortV1,
    ObserveRouteQueryV1,
    RevokeRouteCommandV1,
    RevokeRoutePortV1,
    RouteKeyV1,
    RouteLayerV1,
    RouteOperationV1,
    RouteOutcomeV1,
    RoutePresenceV1,
    RouteProjectionV1,
    RouteReasonCodeV1,
    RouteTrafficBudgetV1,
    RouteVersionV1,
    RuntimeControlModeV1,
    UpdateRouteCommandV1,
    UpdateRoutePortV1,
)


NOW_MS = 1_900_000_000_000


class ManualClockV1:
    def __init__(self, now_ms: int = NOW_MS) -> None:
        self.value = now_ms

    def now_ms(self) -> int:
        return self.value

    def advance(self, milliseconds: int) -> None:
        self.value += milliseconds


@dataclass(frozen=True)
class RouteHarnessV1:
    adapter: object
    clock: ManualClockV1
    faults: ScriptedRouteFaultPlanV1


def _version(number: int, *, fencing_token: str | None = None) -> RouteVersionV1:
    return RouteVersionV1(
        projection_version=number,
        route_epoch=number,
        topology_epoch=number,
        key_epoch=number,
        fencing_token=fencing_token or f"hub-fence-{number}",
    )


def _projection(
    number: int = 1,
    *,
    receiver_refs: tuple[str, ...] = ("A" * 22,),
    layer: str = "low",
    expires_at_ms: int = NOW_MS + 5_000,
) -> RouteProjectionV1:
    version = _version(number)
    return RouteProjectionV1(
        key=RouteKeyV1(tenant_ref="tenant-contract", room_ref="room-contract", route_id="route-contract"),
        group_ref="group-contract",
        group_revision=number,
        group_member_digest=f"{number:064x}",
        snapshot_ref=f"snapshot-contract-{number}",
        audience_projection_version=number,
        audience_digest=("A" * 42) + str(number % 10),
        receiver_refs=receiver_refs,
        runtime_control_mode=RuntimeControlModeV1.LIVEKIT_CONTROL_API,
        cluster_ref="cluster-contract",
        region_ref="region-contract",
        runtime_instance_ref=None,
        publication_ref="publication-contract",
        media_kind=MediaKindV1.VIDEO,
        allowed_layers=(RouteLayerV1(layer_ref=layer, rid=layer, spatial_id=number % 3, temporal_id=0),),
        traffic_budgets=(
            RouteTrafficBudgetV1(
                traffic_class="media",
                max_bitrate_bps=1_000_000 + number,
                max_packets_per_second=2_000,
                max_burst_bytes=64_000,
            ),
        ),
        max_total_bitrate_bps=2_000_000,
        issued_at_ms=NOW_MS,
        expires_at_ms=expires_at_ms,
        version=version,
        intent_digest=f"{number + 100:064x}",
    )


class RoutePortContractV1:
    """Inherit this suite and provide ``route_harness`` for a real adapter."""

    @pytest.fixture
    def route_harness(self) -> RouteHarnessV1:
        raise NotImplementedError

    def test_exposes_four_segregated_ports(self, route_harness: RouteHarnessV1) -> None:
        adapter = route_harness.adapter
        assert isinstance(adapter, ApplyRoutePortV1)
        assert isinstance(adapter, UpdateRoutePortV1)
        assert isinstance(adapter, RevokeRoutePortV1)
        assert isinstance(adapter, ObserveRoutePortV1)

    def test_apply_acknowledges_and_observes_exact_projection(self, route_harness: RouteHarnessV1) -> None:
        desired = _projection()
        result = route_harness.adapter.apply(ApplyRouteCommandV1("apply-1", desired))

        assert result.outcome is RouteOutcomeV1.ACKNOWLEDGED
        assert result.reason_code is RouteReasonCodeV1.ACKNOWLEDGED
        assert result.observed_version == desired.version
        observed = route_harness.adapter.observe(ObserveRouteQueryV1(desired.key))
        assert observed.presence is RoutePresenceV1.ACTIVE
        assert observed.projection == desired

    def test_update_is_absolute_not_union_or_adapter_expansion(self, route_harness: RouteHarnessV1) -> None:
        original = _projection(receiver_refs=("A" * 22,), layer="low")
        desired = _projection(2, receiver_refs=("B" * 22,), layer="high", expires_at_ms=NOW_MS + 4_000)
        route_harness.adapter.apply(ApplyRouteCommandV1("apply-absolute", original))

        result = route_harness.adapter.update(UpdateRouteCommandV1("update-absolute", original.version, desired))

        assert result.outcome is RouteOutcomeV1.ACKNOWLEDGED
        observed = route_harness.adapter.observe(ObserveRouteQueryV1(original.key))
        assert observed.projection == desired
        assert observed.projection.receiver_refs == ("B" * 22,)
        assert observed.projection.allowed_layers == desired.allowed_layers
        assert observed.projection.expires_at_ms == desired.expires_at_ms
        assert observed.projection.version == desired.version

    def test_stale_fence_rejects_without_changing_projection(self, route_harness: RouteHarnessV1) -> None:
        original = _projection()
        desired = _projection(2)
        route_harness.adapter.apply(ApplyRouteCommandV1("apply-fence", original))
        wrong_fence = replace(original.version, fencing_token="different-hub-fence")

        result = route_harness.adapter.update(UpdateRouteCommandV1("update-stale-fence", wrong_fence, desired))

        assert result.outcome is RouteOutcomeV1.REJECTED
        assert result.reason_code is RouteReasonCodeV1.STALE_FENCING
        assert route_harness.adapter.observe(ObserveRouteQueryV1(original.key)).projection == original

    def test_revoke_is_fenced_and_leaves_exact_tombstone(self, route_harness: RouteHarnessV1) -> None:
        desired = _projection()
        revoke_version = _version(2)
        route_harness.adapter.apply(ApplyRouteCommandV1("apply-revoke", desired))

        result = route_harness.adapter.revoke(
            RevokeRouteCommandV1("revoke-1", desired.key, desired.version, revoke_version, NOW_MS)
        )

        assert result.outcome is RouteOutcomeV1.ACKNOWLEDGED
        observed = route_harness.adapter.observe(ObserveRouteQueryV1(desired.key))
        assert observed.presence is RoutePresenceV1.ABSENT
        assert observed.projection is None
        assert observed.tombstone_version == revoke_version

    def test_reapply_cannot_cross_a_newer_tombstone(self, route_harness: RouteHarnessV1) -> None:
        desired = _projection()
        route_harness.adapter.apply(ApplyRouteCommandV1("apply-before-tombstone", desired))
        route_harness.adapter.revoke(
            RevokeRouteCommandV1("revoke-before-reapply", desired.key, desired.version, _version(2), NOW_MS)
        )

        result = route_harness.adapter.apply(ApplyRouteCommandV1("stale-reapply", desired))

        assert result.outcome is RouteOutcomeV1.REJECTED
        assert result.reason_code is RouteReasonCodeV1.STALE_ROUTE_EPOCH

    def test_expired_projection_fails_closed(self, route_harness: RouteHarnessV1) -> None:
        expired = _projection(expires_at_ms=NOW_MS + 1)
        route_harness.clock.advance(1)

        result = route_harness.adapter.apply(ApplyRouteCommandV1("apply-expired", expired))

        assert result.outcome is RouteOutcomeV1.REJECTED
        assert result.reason_code is RouteReasonCodeV1.EXPIRED


class DeterministicRouteFaultContractV1:
    @pytest.fixture
    def route_harness(self) -> RouteHarnessV1:
        raise NotImplementedError

    def test_explicit_ack(self, route_harness: RouteHarnessV1) -> None:
        route_harness.faults.push(RouteFaultV1.ACK)
        result = route_harness.adapter.apply(ApplyRouteCommandV1("fault-ack", _projection()))
        assert result.reason_code is RouteReasonCodeV1.ACKNOWLEDGED

    def test_duplicate_delivery_is_idempotent(self, route_harness: RouteHarnessV1) -> None:
        desired = _projection()
        route_harness.faults.push(RouteFaultV1.DUPLICATE)

        result = route_harness.adapter.apply(ApplyRouteCommandV1("fault-duplicate", desired))

        assert result.reason_code is RouteReasonCodeV1.DUPLICATE_IDEMPOTENT
        assert route_harness.adapter.observe(ObserveRouteQueryV1(desired.key)).projection == desired

    def test_reorder_rejects_without_mutation(self, route_harness: RouteHarnessV1) -> None:
        original = _projection()
        desired = _projection(2)
        route_harness.adapter.apply(ApplyRouteCommandV1("fault-reorder-setup", original))
        route_harness.faults.push(RouteFaultV1.REORDER)

        result = route_harness.adapter.update(UpdateRouteCommandV1("fault-reorder", original.version, desired))

        assert result.reason_code is RouteReasonCodeV1.COMMAND_REORDERED
        assert route_harness.adapter.observe(ObserveRouteQueryV1(original.key)).projection == original

    def test_timeout_after_commit_is_reconcilable_and_retry_idempotent(self, route_harness: RouteHarnessV1) -> None:
        desired = _projection()
        command = ApplyRouteCommandV1("fault-timeout", desired)
        route_harness.faults.push(RouteFaultV1.TIMEOUT)

        result = route_harness.adapter.apply(command)

        assert result.outcome is RouteOutcomeV1.UNKNOWN
        assert result.reason_code is RouteReasonCodeV1.TIMEOUT
        assert route_harness.adapter.observe(ObserveRouteQueryV1(desired.key)).projection == desired
        retry = route_harness.adapter.apply(command)
        assert retry.reason_code is RouteReasonCodeV1.DUPLICATE_IDEMPOTENT

    def test_injected_stale_fencing_is_fail_closed(self, route_harness: RouteHarnessV1) -> None:
        original = _projection()
        desired = _projection(2)
        route_harness.adapter.apply(ApplyRouteCommandV1("fault-fence-setup", original))
        route_harness.faults.push(RouteFaultV1.STALE_FENCING)

        result = route_harness.adapter.update(UpdateRouteCommandV1("fault-fence", original.version, desired))

        assert result.reason_code is RouteReasonCodeV1.STALE_FENCING
        assert route_harness.adapter.observe(ObserveRouteQueryV1(original.key)).projection == original

    def test_partial_apply_rolls_back_all_fields(self, route_harness: RouteHarnessV1) -> None:
        original = _projection()
        desired = _projection(2, receiver_refs=("B" * 22, "C" * 22), layer="high")
        route_harness.adapter.apply(ApplyRouteCommandV1("fault-partial-setup", original))
        route_harness.faults.push(RouteFaultV1.PARTIAL)

        result = route_harness.adapter.update(UpdateRouteCommandV1("fault-partial", original.version, desired))

        assert result.reason_code is RouteReasonCodeV1.PARTIAL_APPLY_ROLLED_BACK
        assert route_harness.adapter.observe(ObserveRouteQueryV1(original.key)).projection == original

    def test_node_loss_hides_state_until_explicit_recovery(self, route_harness: RouteHarnessV1) -> None:
        desired = _projection()
        route_harness.adapter.apply(ApplyRouteCommandV1("fault-loss-setup", desired))
        route_harness.faults.push(RouteFaultV1.NODE_LOSS)

        lost = route_harness.adapter.observe(ObserveRouteQueryV1(desired.key))

        assert lost.presence is RoutePresenceV1.UNKNOWN
        assert lost.reason_code is RouteReasonCodeV1.RUNTIME_UNAVAILABLE
        assert lost.projection is None
        route_harness.faults.push(RouteFaultV1.RECOVERY)
        recovered = route_harness.adapter.observe(ObserveRouteQueryV1(desired.key))
        assert recovered.presence is RoutePresenceV1.ACTIVE
        assert recovered.reason_code is RouteReasonCodeV1.RUNTIME_RECOVERED
        assert recovered.projection == desired


class TestDeterministicSfuBroadcastRouteMockAdapter(RoutePortContractV1, DeterministicRouteFaultContractV1):
    @pytest.fixture
    def route_harness(self) -> RouteHarnessV1:
        clock = ManualClockV1()
        faults = ScriptedRouteFaultPlanV1()
        return RouteHarnessV1(
            adapter=DeterministicSfuBroadcastRouteMockAdapter(clock=clock, fault_plan=faults),
            clock=clock,
            faults=faults,
        )
