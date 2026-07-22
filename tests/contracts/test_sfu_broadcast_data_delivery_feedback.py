from agent.services.sfu_broadcast_data_queue_policy import (
    AggregateQueueLimits,
    BoundedSfuBroadcastDataQueue,
    DataTrafficClass,
    DeliveryScope,
    QueueAdapterKind,
    QueueCleanupProfile,
    QueueDeliveryFeedback,
    QueueDeliveryOutcome,
    QueueLimits,
    QueueOffer,
    QueueOverflowAction,
    QueueReasonCodes,
    SfuBroadcastDataQueueProfile,
    TrafficAuthority,
    TrafficClassProfile,
    TrafficKindRule,
)


def _queue() -> BoundedSfuBroadcastDataQueue:
    aggregate = AggregateQueueLimits(1024, 8, 1000, 8)
    traffic_class = TrafficClassProfile(
        DataTrafficClass.CONTROL_HINT, 1, QueueOverflowAction.DROP,
        "SFB_DATA_CONTROL_HINT_DROPPED", False, QueueLimits(512, 4, 500, 500, 4),
    )
    profile = SfuBroadcastDataQueueProfile(
        "test-profile", "1.0.0", {DataTrafficClass.CONTROL_HINT: traffic_class},
        aggregate, aggregate, aggregate,
        {"control_hint": TrafficKindRule(
            "control_hint", DataTrafficClass.CONTROL_HINT,
            TrafficAuthority.NON_AUTHORITATIVE, DeliveryScope.AUTHORIZED_GROUP,
            True, "SFB_DATA_KIND_CONTROL_HINT_ALLOWED",
        )},
        QueueReasonCodes(
            "ACCEPTED", "EVICTED", "COALESCED", "UNKNOWN", "FORBIDDEN",
            "INVALID", "BLOCKED", "EXPIRED",
        ),
        QueueCleanupProfile(10, 1000),
    )
    return BoundedSfuBroadcastDataQueue(
        profile, adapter_kind=QueueAdapterKind.HUB_SEND_DATA_ADAPTER,
        adapter_handle="hub-a",
    )


def test_delivery_feedback_releases_metadata_and_is_idempotent() -> None:
    queue = _queue()
    decision = queue.enqueue(QueueOffer(
        "msg-a", "room-a", "receiver-a", "control_hint", 64, 10, 1, 100,
    ), now_ms=100)
    assert decision.accepted
    feedback = QueueDeliveryFeedback(
        "receipt-a", "msg-a", "receiver-a", QueueDeliveryOutcome.DELIVERED, 101, 64,
    )
    first = queue.acknowledge_delivery(feedback, now_ms=101)
    replay = queue.acknowledge_delivery(feedback, now_ms=101)
    assert first.accepted and first.message_removed
    assert replay.accepted and replay.duplicate
    assert queue.snapshot().usage.messages == 0


def test_unknown_feedback_never_removes_or_allocates_payload() -> None:
    queue = _queue()
    queue.enqueue(QueueOffer(
        "msg-a", "room-a", "receiver-a", "control_hint", 64, 10, 1, 100,
    ), now_ms=100)
    result = queue.acknowledge_delivery(QueueDeliveryFeedback(
        "receipt-a", "msg-a", "receiver-a", QueueDeliveryOutcome.UNKNOWN, 101,
    ), now_ms=101)
    assert not result.accepted and result.retryable
    assert queue.snapshot().usage.messages == 1
