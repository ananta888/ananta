from dataclasses import replace

import pytest

from agent.services.sfu_node_drain_service import (
    InMemorySfuNodeDrainRepository,
    SfuExistingRoomPolicy,
    SfuNodeDrainError,
    SfuNodeDrainService,
    SfuNodeDrainState,
    SfuNodeVersionSet,
    SfuRoomDrainResult,
    SfuVersionCompatibilityMatrix,
)


class Admission:
    def __init__(self):
        self.events = []

    def stop_admission(self, record, operation_id):
        self.events.append(("stop", record.node_id, operation_id))
        return True

    def resume_admission(self, record, operation_id):
        self.events.append(("resume", record.node_id, operation_id))
        return True


class Rooms:
    def __init__(self, remaining=(1, 0)):
        self.remaining = iter(remaining)
        self.events = []

    def apply(self, record, policy, operation_id):
        self.events.append((record.node_id, policy.value, operation_id))
        return SfuRoomDrainResult(next(self.remaining), True, True, True)


def versions():
    return SfuNodeVersionSet(
        contract_version="1",
        adapter_name="livekit",
        adapter_version="1.9",
        e2ee_version="1",
        route_version="ananta.sfu-broadcast-route-port.v1",
    )


def service(rooms=None, clock=lambda: 100.0):
    repository = InMemorySfuNodeDrainRepository()
    admission = Admission()
    room_port = rooms or Rooms()
    matrix = SfuVersionCompatibilityMatrix.from_file(
        "config/sfu_broadcast_version_compatibility.json"
    )
    return (
        SfuNodeDrainService(
            repository, admission, room_port, matrix, clock=clock
        ),
        repository,
        admission,
        room_port,
    )


def test_rolling_upgrade_stops_admission_before_bounded_room_migration():
    drain, _, admission, rooms = service()
    first = drain.request(
        tenant_id="tenant",
        cluster_id="cluster",
        node_id="node-1",
        versions=versions(),
        room_policy=SfuExistingRoomPolicy.CONTROLLED_REJOIN,
        reason_code="rolling_upgrade",
        active_rooms=2,
    )
    with pytest.raises(SfuNodeDrainError, match="sfu_drain_parallel_limit"):
        drain.request(
            tenant_id="tenant",
            cluster_id="cluster",
            node_id="node-2",
            versions=versions(),
            room_policy=SfuExistingRoomPolicy.CONTROLLED_REJOIN,
            reason_code="rolling_upgrade",
            active_rooms=1,
        )
    stopped = drain.advance(first)
    draining = drain.advance(stopped)
    drained = drain.advance(draining)
    assert stopped.state is SfuNodeDrainState.ADMISSION_STOPPED
    assert draining.state is SfuNodeDrainState.DRAINING
    assert drained.state is SfuNodeDrainState.DRAINED
    assert admission.events[0][0] == "stop"
    assert rooms.events[0][1] == "controlled_rejoin"


def test_unknown_version_fails_closed_and_deadline_forces_parent_fallback():
    now = [100.0]
    drain, _, _, rooms = service(rooms=Rooms((0,)), clock=lambda: now[0])
    with pytest.raises(SfuNodeDrainError, match="sfu_drain_version_incompatible"):
        drain.request(
            tenant_id="tenant",
            cluster_id="cluster",
            node_id="unknown",
            versions=replace(versions(), e2ee_version="unknown"),
            room_policy=SfuExistingRoomPolicy.HOLD,
            reason_code="upgrade",
            active_rooms=1,
        )
    record = drain.request(
        tenant_id="tenant",
        cluster_id="cluster",
        node_id="node-1",
        versions=versions(),
        room_policy=SfuExistingRoomPolicy.HOLD,
        reason_code="upgrade",
        active_rooms=1,
    )
    stopped = drain.advance(record)
    now[0] = stopped.deadline_at
    forced = drain.advance(stopped)
    assert forced.state is SfuNodeDrainState.FORCED
    assert rooms.events[-1][1] == "parent_fallback"
