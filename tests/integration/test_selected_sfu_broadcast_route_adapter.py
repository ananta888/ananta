from __future__ import annotations

from dataclasses import replace

from agent.adapters.livekit_broadcast_route_adapter import (
    LiveKitBroadcastRouteAdapter,
    LiveKitOperationReservation,
    LiveKitOperationReservationStatus,
    LiveKitRouteAuthorization,
)
from agent.adapters.livekit_control_api_client import (
    LiveKitControlApiClient,
    LiveKitControlApiConfig,
    LiveKitRouteBinding,
    TwirpResponse,
)
from agent.services.sfu_broadcast_route_port import (
    ApplyRouteCommandV1,
    MediaKindV1,
    ObserveRouteQueryV1,
    RouteKeyV1,
    RouteLayerV1,
    RouteOutcomeV1,
    RoutePresenceV1,
    RouteProjectionV1,
    RouteReasonCodeV1,
    RouteTrafficBudgetV1,
    RouteVersionV1,
    RuntimeControlModeV1,
    UpdateRouteCommandV1,
)


NOW_MS = 1_900_000_000_000


class Clock:
    def now_ms(self):
        return NOW_MS


class EndpointIdentity:
    verified = True

    def verify(self):
        return self.verified


class Bindings:
    def desired(self, projection):
        return LiveKitRouteBinding(
            projection.key.room_ref,
            tuple(f"receiver-{index}" for index, _ in enumerate(projection.receiver_refs)),
            ("TR_camera",),
            True,
        )

    def existing(self, key, version):
        del version
        return LiveKitRouteBinding(
            key.room_ref, ("receiver-0", "receiver-1", "receiver-2"), ("TR_camera",), True
        )

    def observed(self, key):
        return LiveKitRouteBinding(key.room_ref, ("receiver-0",), ("TR_camera",), True)


class Transport:
    def __init__(self, statuses=()):
        self.statuses = list(statuses)
        self.calls = []

    def post(self, **request):
        self.calls.append(request)
        status = self.statuses.pop(0) if self.statuses else 200
        payload = {"participants": [{"identity": "receiver-0"}]} if request["url"].endswith("ListParticipants") else {}
        return TwirpResponse(status, payload, 10)


class Authorizer:
    def __init__(self):
        self.current = None
        self.signature_verified = True

    def authorize(self, *, operation, command, command_digest):
        del operation
        projection = getattr(command, "desired", None) or self.projection
        return LiveKitRouteAuthorization(
            True,
            command_digest,
            projection,
            self.signature_verified,
            True,
            current_version=self.current,
        )


class ObservationAuthorization:
    def authorize_observation(self, query):
        del query
        return True


class Ledger:
    def __init__(self):
        self.records = {}
        self.results = {}

    def reserve(self, record):
        existing = self.records.get(record.operation_id)
        if existing is None:
            self.records[record.operation_id] = record
            return LiveKitOperationReservation(LiveKitOperationReservationStatus.CREATED)
        status = (
            LiveKitOperationReservationStatus.DUPLICATE
            if existing == record
            else LiveKitOperationReservationStatus.CONFLICT
        )
        return LiveKitOperationReservation(
            status, existing, self.results.get(record.operation_id)
        )

    def complete(self, record, result):
        self.results[record.operation_id] = result


def version(number):
    return RouteVersionV1(number, number, number, number, f"fence-{number}")


def projection(number=1):
    return RouteProjectionV1(
        RouteKeyV1("tenant-a", "room-a", "route-a"),
        "group-a",
        number,
        f"{number:064x}",
        f"snapshot-{number}",
        number,
        ("A" * 42) + str(number),
        ("A" * 22, "B" * 22, "C" * 22),
        RuntimeControlModeV1.LIVEKIT_CONTROL_API,
        "cluster-a",
        "region-a",
        None,
        "publication-a",
        MediaKindV1.VIDEO,
        (RouteLayerV1("low", 0, 0, "q"),),
        (RouteTrafficBudgetV1("media", 1_000_000, 2_000, 64_000),),
        2_000_000,
        NOW_MS - 100,
        NOW_MS + 5_000,
        version(number),
        f"{number + 100:064x}",
    )


def harness(statuses=()):
    bindings = Bindings()
    transport = Transport(statuses)
    client = LiveKitControlApiClient(
        LiveKitControlApiConfig(
            "https://livekit.internal.example", "ananta-control", "s" * 32
        ),
        bindings,
        observation_bindings=bindings,
        transport=transport,
        clock=lambda: NOW_MS / 1000,
    )
    authorizer = Authorizer()
    adapter = LiveKitBroadcastRouteAdapter(
        client=client,
        command_authorization=authorizer,
        observation_authorization=ObservationAuthorization(),
        endpoint_identity=EndpointIdentity(),
        operation_ledger=Ledger(),
        clock=Clock(),
    )
    return adapter, authorizer, transport


def test_three_receiver_apply_is_persistently_idempotent_but_not_claimed_as_ack():
    adapter, _authorizer, transport = harness()
    command = ApplyRouteCommandV1("apply-a", projection())
    first = adapter.apply(command)
    duplicate = adapter.apply(command)
    assert first.outcome is RouteOutcomeV1.UNKNOWN
    assert first.reason_code is RouteReasonCodeV1.CONTROL_API_ACCEPTED_UNVERIFIED
    assert duplicate.reason_code is RouteReasonCodeV1.DUPLICATE_IDEMPOTENT
    assert len(transport.calls) == 3
    assert adapter.capabilities()["supported"] is False


def test_signature_stale_version_and_partial_apply_fail_closed():
    adapter, authorizer, transport = harness((200, 429, 200))
    authorizer.signature_verified = False
    denied = adapter.apply(ApplyRouteCommandV1("denied", projection()))
    assert denied.reason_code is RouteReasonCodeV1.AUTHORIZATION_FAILED
    assert transport.calls == []
    authorizer.signature_verified = True
    authorizer.current = version(2)
    desired = projection(3)
    stale = adapter.update(UpdateRouteCommandV1("stale", version(1), desired))
    assert stale.reason_code is RouteReasonCodeV1.STALE_ROUTE_EPOCH
    authorizer.current = None
    partial = adapter.apply(ApplyRouteCommandV1("partial", projection()))
    assert partial.reason_code is RouteReasonCodeV1.PARTIAL_APPLY_ROLLED_BACK


def test_public_api_observation_remains_explicitly_non_authoritative():
    adapter, _authorizer, _transport = harness()
    observed = adapter.observe(ObserveRouteQueryV1(projection().key))
    assert observed.presence is RoutePresenceV1.UNKNOWN
    assert observed.reason_code is RouteReasonCodeV1.OBSERVATION_UNSUPPORTED
    assert observed.projection is None
