from __future__ import annotations

import hashlib
import os
import random

import pytest

from agent.services.sfu_broadcast_data_queue_policy import (
    BoundedSfuBroadcastDataQueue,
    QueueAdapterKind,
    QueueOffer,
    load_sfu_broadcast_data_queue_profile,
)
from agent.services.sfu_broadcast_route_port import RuntimeControlModeV1
from agent.services.sfu_fanout_route_lifecycle import (
    FanoutRouteEvent,
    FanoutRouteLifecycleConfig,
    FanoutRouteLifecycleRecord,
    RouteActivationGuards,
    RouteApplyEvidence,
    RouteEpochBinding,
    SfuFanoutRouteLifecycle,
)

ROOT = __import__("pathlib").Path(__file__).resolve().parents[2]


def _seeds() -> tuple[int, ...]:
    return tuple(int(value) for value in os.environ.get(
        "SFU_BROADCAST_FUZZ_SEEDS", "104729,130363,155921"
    ).split(","))


def _cases() -> int:
    return max(1, min(256, int(os.environ.get("SFU_BROADCAST_FUZZ_CASES_PER_SEED", "64"))))


def _record() -> FanoutRouteLifecycleRecord:
    return FanoutRouteLifecycleRecord.persisted_intent(
        route_id="route-a", tenant_id="tenant-a", room_ref="room-a",
        runtime_scope_ref="cluster-a", runtime_control_mode=RuntimeControlModeV1.LIVEKIT_CONTROL_API,
        intent_digest="a" * 64, idempotency_key="intent-a", nonce="nonce-a",
        intent_sequence=7, projection_version=3,
        epochs=RouteEpochBinding(2, 3, 4, 5, 6), fencing_token="fence-7",
        issued_at_ms=1_000, expires_at_ms=50_000,
    )


def _evidence() -> RouteApplyEvidence:
    return RouteApplyEvidence(
        operation_id="ack", idempotency_key="intent-a", nonce="nonce-a", sequence=7,
        projection_version=3, expires_at_ms=40_000, tenant_id="tenant-a",
        room_ref="room-a", runtime_scope_ref="cluster-a", intent_digest="a" * 64,
        fencing_token="fence-7", route_epoch=5,
        runtime_control_mode=RuntimeControlModeV1.LIVEKIT_CONTROL_API,
        tls_bound=True, api_credential_bound=True, reconciliation_confirmed=True,
    )


@pytest.mark.parametrize("seed", _seeds())
def test_route_state_machine_random_event_sequences_remain_bounded(seed: int) -> None:
    random_source = random.Random(seed)
    lifecycle = SfuFanoutRouteLifecycle(FanoutRouteLifecycleConfig())
    record = _record()
    for index in range(_cases()):
        event = random_source.choice(tuple(FanoutRouteEvent))
        kwargs = {}
        if event is FanoutRouteEvent.ACK:
            kwargs["evidence"] = _evidence()
        if event is FanoutRouteEvent.ACTIVATE:
            kwargs["guards"] = RouteActivationGuards(True, True, True)
        result = lifecycle.transition(
            record,
            event,
            operation_id=f"op-{seed}-{index}",
            request_digest=hashlib.sha256(f"{seed}:{index}".encode()).hexdigest(),
            now_ms=1_100 + index,
            **kwargs,
        )
        assert len(result.audit_digest) == 64
        assert result.record.projection_version >= record.projection_version
        record = result.record


@pytest.mark.parametrize("seed", _seeds())
def test_queue_flood_priority_and_blocked_receiver_never_exceed_caps(seed: int) -> None:
    profile = load_sfu_broadcast_data_queue_profile(
        ROOT / "config/sfu_broadcast_egress_limits.json"
    )
    queue = BoundedSfuBroadcastDataQueue(
        profile,
        adapter_kind=QueueAdapterKind.HUB_SEND_DATA_ADAPTER,
        adapter_handle=f"fuzz-adapter-{seed}",
    )
    random_source = random.Random(seed)
    forbidden = (
        "authoritative_membership_mutation",
        "authoritative_key_mutation",
        "training_payload",
        "dataset_payload",
        "evidence_payload",
        "unknown_future_kind",
    )
    allowed = tuple(item.value for item in profile.per_destination_class)
    for index in range(_cases() * 4):
        traffic_kind = random_source.choice((*allowed, *forbidden))
        decision = queue.enqueue(
            QueueOffer(
                message_id=f"message-{seed}-{index}",
                room_handle=f"room-{index % 4}",
                destination_handle=f"receiver-{index % 32}",
                traffic_kind=traffic_kind,
                queue_bytes=random_source.randint(1, 4096),
                buffered_duration_ms=random_source.randint(0, 200),
                chunk_count=random_source.randint(1, 8),
                enqueued_at_ms=index,
                coalesce_key=f"coalesce-{index % 8}",
            ),
            now_ms=index,
        )
        if traffic_kind in forbidden:
            assert decision.accepted is False
        snapshot = queue.snapshot()
        limits = profile.hub_send_data_adapter_limits
        assert snapshot.usage.messages <= limits.messages_max
        assert snapshot.usage.queue_bytes <= limits.queue_bytes_max
        assert snapshot.usage.chunk_count <= limits.chunk_count_max
    queue.mark_blocked(now_ms=10_000)
    queue.cleanup(now_ms=10_000 + profile.cleanup.disconnect_after_blocked_ms)
    assert queue.snapshot().usage.messages == 0

