"""Bounded, payload-blind queue policy for SFU broadcast data fanout.

The hub owns classification and policy. Browser and hub SendData adapters each
instantiate a ledger for their own boundary; the ledger never receives or
stores message content and does not assume an SFU-internal scheduler hook.
"""

from __future__ import annotations

from collections import OrderedDict

from agent.services.sfu_broadcast_data_queue_models import (
    AggregateQueueLimits,
    DataQueuePolicyError,
    DataTrafficClass,
    DeliveryScope,
    _is_handle,
    QueueAdapterKind,
    QueueCleanupProfile,
    QueueCleanupResult,
    QueueDecision,
    QueueDecisionDisposition,
    QueueDeliveryFeedback,
    QueueDeliveryFeedbackResult,
    QueueDeliveryOutcome,
    QueuedMessageMetadata,
    QueueLimits,
    QueueLimitScope,
    QueueOffer,
    QueueOverflowAction,
    QueueReasonCodes,
    QueueSnapshot,
    QueueUsage,
    SfuBroadcastDataQueueProfile,
    TrafficAuthority,
    TrafficClassProfile,
    TrafficKindRule,
)
from agent.services.sfu_broadcast_data_queue_profile_parser import (
    load_sfu_broadcast_data_queue_profile,
    parse_sfu_broadcast_data_queue_profile,
)


class BoundedSfuBroadcastDataQueue:
    """Metadata-only bounded queue shared by browser and hub adapter mocks.

    The instance is scoped to exactly one adapter. Its aggregate adapter cap
    therefore bounds allocation even when it carries several rooms.
    """

    __slots__ = (
        "_adapter_handle",
        "_adapter_kind",
        "_blocked_since_ms",
        "_disconnected",
        "_entries",
        "_profile",
        "_receipts",
    )

    def __init__(
        self,
        profile: SfuBroadcastDataQueueProfile,
        *,
        adapter_kind: QueueAdapterKind,
        adapter_handle: str,
    ) -> None:
        if not _is_handle(adapter_handle):
            raise DataQueuePolicyError("adapter_handle must be a non-empty handle")
        self._profile = profile
        self._adapter_kind = adapter_kind
        self._adapter_handle = adapter_handle
        self._entries: list[QueuedMessageMetadata] = []
        self._receipts: OrderedDict[str, tuple[tuple[object, ...], QueueDeliveryFeedbackResult]] = OrderedDict()
        self._blocked_since_ms: int | None = None
        self._disconnected = False

    def enqueue(self, offer: QueueOffer, *, now_ms: int) -> QueueDecision:
        """Apply cleanup, classification, and all caps before allocating."""

        self.cleanup(now_ms=now_ms)
        reason_codes = self._profile.reason_codes
        if self._disconnected or self._blocked_timeout_reached(now_ms):
            self._disconnect()
            return QueueDecision(
                disposition=QueueDecisionDisposition.REJECT,
                reason_code=reason_codes.blocked_timeout,
                traffic_class=None,
                overflow_action=QueueOverflowAction.DISCONNECT,
                limit_scope=QueueLimitScope.BLOCKED_TIMEOUT,
            )

        rule = self._profile.traffic_kinds.get(offer.traffic_kind)
        if rule is None:
            return QueueDecision(
                disposition=QueueDecisionDisposition.REJECT,
                reason_code=reason_codes.unknown_traffic_kind,
                traffic_class=None,
                overflow_action=None,
                limit_scope=None,
            )
        if not rule.allowed or rule.traffic_class is None:
            return QueueDecision(
                disposition=QueueDecisionDisposition.REJECT,
                reason_code=rule.reason_code or reason_codes.forbidden_traffic_kind,
                traffic_class=None,
                overflow_action=None,
                limit_scope=None,
            )

        class_profile = self._profile.per_destination_class[rule.traffic_class]
        if not self._valid_offer(offer, class_profile, now_ms):
            return QueueDecision(
                disposition=QueueDecisionDisposition.REJECT,
                reason_code=reason_codes.invalid_metadata,
                traffic_class=rule.traffic_class,
                overflow_action=None,
                limit_scope=None,
            )

        age_ms = now_ms - offer.enqueued_at_ms
        if age_ms > class_profile.limits.age_ms_max:
            return self._overflow_decision(class_profile, QueueLimitScope.AGE)

        entry = QueuedMessageMetadata(
            message_id=offer.message_id,
            room_handle=offer.room_handle,
            destination_handle=offer.destination_handle,
            traffic_class=rule.traffic_class,
            priority=class_profile.priority,
            queue_bytes=offer.queue_bytes,
            buffered_duration_ms=offer.buffered_duration_ms,
            chunk_count=offer.chunk_count,
            enqueued_at_ms=offer.enqueued_at_ms,
            coalesce_key=offer.coalesce_key,
        )

        retained = list(self._entries)
        coalesced: list[QueuedMessageMetadata] = []
        if class_profile.overflow_action is QueueOverflowAction.COALESCE:
            coalesced = [
                queued
                for queued in retained
                if _same_coalesce_bucket(queued, entry)
            ]
            if coalesced:
                coalesced_ids = {queued.message_id for queued in coalesced}
                retained = [
                    queued
                    for queued in retained
                    if queued.message_id not in coalesced_ids
                ]

        limit_scope = self._first_exceeded_scope(retained, entry)
        evicted: list[QueuedMessageMetadata] = []
        while limit_scope is not None:
            candidate = self._priority_eviction_candidate(
                retained,
                incoming=entry,
                exceeded_scope=limit_scope,
            )
            if candidate is None:
                return self._overflow_decision(class_profile, limit_scope)
            retained.remove(candidate)
            evicted.append(candidate)
            limit_scope = self._first_exceeded_scope(retained, entry)

        retained.append(entry)
        self._entries = retained
        removed = tuple(
            queued.message_id for queued in (*coalesced, *evicted)
        )
        if coalesced:
            return QueueDecision(
                disposition=QueueDecisionDisposition.ENQUEUE,
                reason_code=reason_codes.coalesced,
                traffic_class=rule.traffic_class,
                overflow_action=QueueOverflowAction.COALESCE,
                limit_scope=None,
                removed_message_ids=removed,
            )
        if evicted:
            return QueueDecision(
                disposition=QueueDecisionDisposition.ENQUEUE,
                reason_code=reason_codes.accepted_after_priority_eviction,
                traffic_class=rule.traffic_class,
                overflow_action=QueueOverflowAction.DROP,
                limit_scope=None,
                removed_message_ids=removed,
            )
        return QueueDecision(
            disposition=QueueDecisionDisposition.ENQUEUE,
            reason_code=reason_codes.accepted,
            traffic_class=rule.traffic_class,
            overflow_action=None,
            limit_scope=None,
        )

    def acknowledge_delivery(
        self, feedback: QueueDeliveryFeedback, *, now_ms: int,
    ) -> QueueDeliveryFeedbackResult:
        """Release queue metadata from content-free, idempotent delivery feedback."""

        digest = (
            feedback.message_id, feedback.destination_handle, feedback.outcome.value,
            feedback.observed_at_ms, feedback.published_wire_bytes,
        )
        previous = self._receipts.get(feedback.receipt_id)
        if previous is not None:
            if previous[0] != digest:
                return QueueDeliveryFeedbackResult(
                    False, "SFB_DATA_DELIVERY_RECEIPT_CONFLICT", False,
                )
            result = previous[1]
            return QueueDeliveryFeedbackResult(
                result.accepted, "SFB_DATA_DELIVERY_RECEIPT_DUPLICATE",
                result.message_removed, duplicate=True, retryable=result.retryable,
            )
        if (
            not _is_handle(feedback.receipt_id)
            or not _is_handle(feedback.message_id)
            or not _is_handle(feedback.destination_handle)
            or not _is_non_negative_int(now_ms)
            or not _is_non_negative_int(feedback.observed_at_ms)
            or feedback.observed_at_ms > now_ms
            or (
                feedback.published_wire_bytes is not None
                and not _is_non_negative_int(feedback.published_wire_bytes)
            )
        ):
            return QueueDeliveryFeedbackResult(
                False, "SFB_DATA_DELIVERY_RECEIPT_INVALID", False,
            )
        index = next((
            position for position, entry in enumerate(self._entries)
            if entry.message_id == feedback.message_id
            and entry.destination_handle == feedback.destination_handle
        ), None)
        if feedback.outcome is QueueDeliveryOutcome.UNKNOWN:
            result = QueueDeliveryFeedbackResult(
                False, "SFB_DATA_DELIVERY_UNKNOWN", False, retryable=True,
            )
        elif index is None:
            result = QueueDeliveryFeedbackResult(
                False, "SFB_DATA_DELIVERY_MESSAGE_UNKNOWN", False,
            )
        else:
            self._entries.pop(index)
            result = QueueDeliveryFeedbackResult(
                True,
                "SFB_DATA_DELIVERY_ACKNOWLEDGED"
                if feedback.outcome is QueueDeliveryOutcome.DELIVERED
                else "SFB_DATA_DELIVERY_DROPPED",
                True,
            )
        self._receipts[feedback.receipt_id] = (digest, result)
        self._receipts.move_to_end(feedback.receipt_id)
        while len(self._receipts) > 256:
            self._receipts.popitem(last=False)
        return result

    def mark_blocked(self, *, now_ms: int) -> None:
        if not _is_non_negative_int(now_ms):
            raise DataQueuePolicyError("now_ms must be a non-negative integer")
        if self._blocked_since_ms is None:
            self._blocked_since_ms = now_ms

    def mark_writable(self) -> None:
        if not self._disconnected:
            self._blocked_since_ms = None

    def reset_after_reconnect(self) -> None:
        self._entries.clear()
        self._receipts.clear()
        self._blocked_since_ms = None
        self._disconnected = False

    def cleanup(self, *, now_ms: int) -> QueueCleanupResult:
        """Timer entry point; expires stale metadata and fails blocked adapters closed."""

        if not _is_non_negative_int(now_ms):
            raise DataQueuePolicyError("now_ms must be a non-negative integer")
        if self._blocked_timeout_reached(now_ms):
            removed = tuple(entry.message_id for entry in self._entries)
            self._disconnect()
            return QueueCleanupResult(
                reason_code=self._profile.reason_codes.blocked_timeout,
                removed_message_ids=removed,
                disconnect_required=True,
            )

        retained: list[QueuedMessageMetadata] = []
        expired: list[str] = []
        for entry in self._entries:
            limit = self._profile.per_destination_class[
                entry.traffic_class
            ].limits.age_ms_max
            if now_ms - entry.enqueued_at_ms > limit:
                expired.append(entry.message_id)
            else:
                retained.append(entry)
        self._entries = retained
        return QueueCleanupResult(
            reason_code=(
                self._profile.reason_codes.cleanup_expired if expired else None
            ),
            removed_message_ids=tuple(expired),
            disconnect_required=False,
        )

    def snapshot(self) -> QueueSnapshot:
        return QueueSnapshot(
            adapter_kind=self._adapter_kind,
            adapter_handle=self._adapter_handle,
            usage=_usage(self._entries),
            entries=tuple(self._entries),
            blocked_since_ms=self._blocked_since_ms,
            disconnected=self._disconnected,
        )

    def _valid_offer(
        self,
        offer: QueueOffer,
        class_profile: TrafficClassProfile,
        now_ms: int,
    ) -> bool:
        if not all(
            _is_handle(value)
            for value in (offer.message_id, offer.room_handle, offer.destination_handle)
        ):
            return False
        if any(entry.message_id == offer.message_id for entry in self._entries):
            return False
        if not _is_positive_int(offer.queue_bytes):
            return False
        if not _is_non_negative_int(offer.buffered_duration_ms):
            return False
        if not _is_positive_int(offer.chunk_count):
            return False
        if not _is_non_negative_int(offer.enqueued_at_ms) or offer.enqueued_at_ms > now_ms:
            return False
        if offer.coalesce_key is not None and not _is_handle(offer.coalesce_key):
            return False
        if class_profile.coalesce_key_required and offer.coalesce_key is None:
            return False
        return True

    def _first_exceeded_scope(
        self,
        entries: list[QueuedMessageMetadata],
        incoming: QueuedMessageMetadata,
    ) -> QueueLimitScope | None:
        class_entries = [
            entry
            for entry in entries
            if entry.room_handle == incoming.room_handle
            and entry.destination_handle == incoming.destination_handle
            and entry.traffic_class is incoming.traffic_class
        ]
        class_limits = self._profile.per_destination_class[
            incoming.traffic_class
        ].limits
        if _would_exceed(_usage(class_entries), incoming, class_limits):
            return QueueLimitScope.DESTINATION_CLASS

        room_entries = [
            entry for entry in entries if entry.room_handle == incoming.room_handle
        ]
        if _would_exceed(_usage(room_entries), incoming, self._profile.room_limits):
            return QueueLimitScope.ROOM

        if _would_exceed(
            _usage(entries),
            incoming,
            self._profile.adapter_limits(self._adapter_kind),
        ):
            if self._adapter_kind is QueueAdapterKind.BROWSER_INSTANCE:
                return QueueLimitScope.BROWSER_INSTANCE
            return QueueLimitScope.HUB_SEND_DATA_ADAPTER
        return None

    def _priority_eviction_candidate(
        self,
        entries: list[QueuedMessageMetadata],
        *,
        incoming: QueuedMessageMetadata,
        exceeded_scope: QueueLimitScope,
    ) -> QueuedMessageMetadata | None:
        candidates = [
            entry
            for entry in entries
            if entry.priority > incoming.priority
            and _contributes_to_scope(entry, incoming, exceeded_scope)
        ]
        if not candidates:
            return None
        return min(
            candidates,
            key=lambda entry: (
                -entry.priority,
                entry.enqueued_at_ms,
                entry.message_id,
            ),
        )

    def _overflow_decision(
        self,
        profile: TrafficClassProfile,
        scope: QueueLimitScope,
    ) -> QueueDecision:
        return QueueDecision(
            disposition=QueueDecisionDisposition.REJECT,
            reason_code=profile.overflow_reason_code,
            traffic_class=profile.traffic_class,
            overflow_action=profile.overflow_action,
            limit_scope=scope,
        )

    def _blocked_timeout_reached(self, now_ms: int) -> bool:
        return (
            self._blocked_since_ms is not None
            and now_ms - self._blocked_since_ms
            >= self._profile.cleanup.disconnect_after_blocked_ms
        )

    def _disconnect(self) -> None:
        self._entries.clear()
        self._receipts.clear()
        self._blocked_since_ms = None
        self._disconnected = True


def _usage(entries: list[QueuedMessageMetadata]) -> QueueUsage:
    return QueueUsage(
        queue_bytes=sum(entry.queue_bytes for entry in entries),
        messages=len(entries),
        buffered_duration_ms=sum(entry.buffered_duration_ms for entry in entries),
        chunk_count=sum(entry.chunk_count for entry in entries),
    )


def _would_exceed(
    usage: QueueUsage,
    incoming: QueuedMessageMetadata,
    limits: QueueLimits | AggregateQueueLimits,
) -> bool:
    return (
        usage.queue_bytes + incoming.queue_bytes > limits.queue_bytes_max
        or usage.messages + 1 > limits.messages_max
        or usage.buffered_duration_ms + incoming.buffered_duration_ms
        > limits.buffered_duration_ms_max
        or usage.chunk_count + incoming.chunk_count > limits.chunk_count_max
    )


def _same_coalesce_bucket(
    queued: QueuedMessageMetadata,
    incoming: QueuedMessageMetadata,
) -> bool:
    return (
        incoming.coalesce_key is not None
        and queued.room_handle == incoming.room_handle
        and queued.destination_handle == incoming.destination_handle
        and queued.traffic_class is incoming.traffic_class
        and queued.coalesce_key == incoming.coalesce_key
    )


def _contributes_to_scope(
    queued: QueuedMessageMetadata,
    incoming: QueuedMessageMetadata,
    scope: QueueLimitScope,
) -> bool:
    if scope is QueueLimitScope.ROOM:
        return queued.room_handle == incoming.room_handle
    if scope in {
        QueueLimitScope.BROWSER_INSTANCE,
        QueueLimitScope.HUB_SEND_DATA_ADAPTER,
    }:
        return True
    return False


def _is_positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _is_non_negative_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


__all__ = [
    "AggregateQueueLimits",
    "BoundedSfuBroadcastDataQueue",
    "DataQueuePolicyError",
    "DataTrafficClass",
    "DeliveryScope",
    "QueueAdapterKind",
    "QueueCleanupProfile",
    "QueueCleanupResult",
    "QueueDecision",
    "QueueDecisionDisposition",
    "QueueDeliveryFeedback",
    "QueueDeliveryFeedbackResult",
    "QueueDeliveryOutcome",
    "QueueLimitScope",
    "QueueLimits",
    "QueueOffer",
    "QueueOverflowAction",
    "QueueReasonCodes",
    "QueueSnapshot",
    "QueueUsage",
    "QueuedMessageMetadata",
    "SfuBroadcastDataQueueProfile",
    "TrafficAuthority",
    "TrafficClassProfile",
    "TrafficKindRule",
    "load_sfu_broadcast_data_queue_profile",
    "parse_sfu_broadcast_data_queue_profile",
]
