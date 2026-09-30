"""Value types and enums of the payload-blind SFU broadcast data queue policy."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum


class DataQueuePolicyError(ValueError):
    """Raised when a queue profile is malformed or unsafe."""


class DataTrafficClass(str, Enum):
    INTERRUPT = "interrupt"
    PRIVATE_RECOVERY = "private_recovery"
    TRANSCRIPT_REVISION = "transcript_revision"
    CONTROL_HINT = "control_hint"
    SHARED_REFERENCE = "shared_reference"


class QueueAdapterKind(str, Enum):
    BROWSER_INSTANCE = "browser_instance"
    HUB_SEND_DATA_ADAPTER = "hub_send_data_adapter"


class QueueOverflowAction(str, Enum):
    COALESCE = "coalesce"
    DROP = "drop"
    LAYER_DOWNSHIFT = "layer_downshift"
    DISCONNECT = "disconnect"


class QueueLimitScope(str, Enum):
    AGE = "age"
    DESTINATION_CLASS = "destination_class"
    ROOM = "room"
    BROWSER_INSTANCE = "browser_instance"
    HUB_SEND_DATA_ADAPTER = "hub_send_data_adapter"
    BLOCKED_TIMEOUT = "blocked_timeout"


class QueueDecisionDisposition(str, Enum):
    ENQUEUE = "enqueue"
    REJECT = "reject"


class TrafficAuthority(str, Enum):
    NON_AUTHORITATIVE = "non_authoritative"
    AUTHORITATIVE = "authoritative"
    PROHIBITED_PAYLOAD = "prohibited_payload"


class DeliveryScope(str, Enum):
    AUTHORIZED_GROUP = "authorized_group"
    AUTHORIZED_RECEIVER = "authorized_receiver"
    AUTHORIZED_SENDER_RECEIVER_PAIR = "authorized_sender_receiver_pair"
    FORBIDDEN = "forbidden"


@dataclass(frozen=True, slots=True)
class QueueLimits:
    queue_bytes_max: int
    messages_max: int
    age_ms_max: int
    buffered_duration_ms_max: int
    chunk_count_max: int


@dataclass(frozen=True, slots=True)
class AggregateQueueLimits:
    queue_bytes_max: int
    messages_max: int
    buffered_duration_ms_max: int
    chunk_count_max: int


@dataclass(frozen=True, slots=True)
class TrafficClassProfile:
    traffic_class: DataTrafficClass
    priority: int
    overflow_action: QueueOverflowAction
    overflow_reason_code: str
    coalesce_key_required: bool
    limits: QueueLimits


@dataclass(frozen=True, slots=True)
class TrafficKindRule:
    traffic_kind: str
    traffic_class: DataTrafficClass | None
    authority: TrafficAuthority
    delivery_scope: DeliveryScope
    allowed: bool
    reason_code: str


@dataclass(frozen=True, slots=True)
class QueueReasonCodes:
    accepted: str
    accepted_after_priority_eviction: str
    coalesced: str
    unknown_traffic_kind: str
    forbidden_traffic_kind: str
    invalid_metadata: str
    blocked_timeout: str
    cleanup_expired: str


@dataclass(frozen=True, slots=True)
class QueueCleanupProfile:
    sweep_interval_ms: int
    disconnect_after_blocked_ms: int


@dataclass(frozen=True, slots=True)
class SfuBroadcastDataQueueProfile:
    profile_id: str
    profile_version: str
    per_destination_class: Mapping[DataTrafficClass, TrafficClassProfile]
    room_limits: AggregateQueueLimits
    browser_instance_limits: AggregateQueueLimits
    hub_send_data_adapter_limits: AggregateQueueLimits
    traffic_kinds: Mapping[str, TrafficKindRule]
    reason_codes: QueueReasonCodes
    cleanup: QueueCleanupProfile

    def adapter_limits(self, kind: QueueAdapterKind) -> AggregateQueueLimits:
        if kind is QueueAdapterKind.BROWSER_INSTANCE:
            return self.browser_instance_limits
        return self.hub_send_data_adapter_limits


@dataclass(frozen=True, slots=True)
class QueueOffer:
    message_id: str
    room_handle: str
    destination_handle: str
    traffic_kind: str
    queue_bytes: int
    buffered_duration_ms: int
    chunk_count: int
    enqueued_at_ms: int
    coalesce_key: str | None = None


@dataclass(frozen=True, slots=True)
class QueuedMessageMetadata:
    message_id: str
    room_handle: str
    destination_handle: str
    traffic_class: DataTrafficClass
    priority: int
    queue_bytes: int
    buffered_duration_ms: int
    chunk_count: int
    enqueued_at_ms: int
    coalesce_key: str | None


@dataclass(frozen=True, slots=True)
class QueueUsage:
    queue_bytes: int
    messages: int
    buffered_duration_ms: int
    chunk_count: int


@dataclass(frozen=True, slots=True)
class QueueDecision:
    disposition: QueueDecisionDisposition
    reason_code: str
    traffic_class: DataTrafficClass | None
    overflow_action: QueueOverflowAction | None
    limit_scope: QueueLimitScope | None
    removed_message_ids: tuple[str, ...] = ()

    @property
    def accepted(self) -> bool:
        return self.disposition is QueueDecisionDisposition.ENQUEUE


@dataclass(frozen=True, slots=True)
class QueueCleanupResult:
    reason_code: str | None
    removed_message_ids: tuple[str, ...]
    disconnect_required: bool


class QueueDeliveryOutcome(str, Enum):
    DELIVERED = "delivered"
    DROPPED = "dropped"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class QueueDeliveryFeedback:
    receipt_id: str
    message_id: str
    destination_handle: str
    outcome: QueueDeliveryOutcome
    observed_at_ms: int
    published_wire_bytes: int | None = None


@dataclass(frozen=True, slots=True)
class QueueDeliveryFeedbackResult:
    accepted: bool
    reason_code: str
    message_removed: bool
    duplicate: bool = False
    retryable: bool = False


@dataclass(frozen=True, slots=True)
class QueueSnapshot:
    adapter_kind: QueueAdapterKind
    adapter_handle: str
    usage: QueueUsage
    entries: tuple[QueuedMessageMetadata, ...]
    blocked_since_ms: int | None
    disconnected: bool


def _is_handle(value: object) -> bool:
    return isinstance(value, str) and bool(value) and value == value.strip()


__all__ = [
    "AggregateQueueLimits",
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
]
