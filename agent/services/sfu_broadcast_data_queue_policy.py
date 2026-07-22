"""Bounded, payload-blind queue policy for SFU broadcast data fanout.

The hub owns classification and policy. Browser and hub SendData adapters each
instantiate a ledger for their own boundary; the ledger never receives or
stores message content and does not assume an SFU-internal scheduler hook.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any


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


@dataclass(frozen=True, slots=True)
class QueueSnapshot:
    adapter_kind: QueueAdapterKind
    adapter_handle: str
    usage: QueueUsage
    entries: tuple[QueuedMessageMetadata, ...]
    blocked_since_ms: int | None
    disconnected: bool


_ALLOWED_KINDS = frozenset(member.value for member in DataTrafficClass)
_FORBIDDEN_KINDS = frozenset(
    {
        "authoritative_membership_mutation",
        "authoritative_key_mutation",
        "training_payload",
        "dataset_payload",
        "model_adapter_payload",
        "evidence_payload",
    }
)
_AUTHORITATIVE_KINDS = frozenset(
    {"authoritative_membership_mutation", "authoritative_key_mutation"}
)
_REASON_CODE = re.compile(r"^[A-Z][A-Z0-9_]{2,95}$")
_LIMIT_FIELDS = (
    "queue_bytes_max",
    "messages_max",
    "buffered_duration_ms_max",
    "chunk_count_max",
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
        self._disconnected = True


def load_sfu_broadcast_data_queue_profile(
    path: str | Path,
) -> SfuBroadcastDataQueueProfile:
    """Load the JSON infrastructure boundary into an immutable policy."""

    try:
        with Path(path).open("r", encoding="utf-8") as handle:
            document = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise DataQueuePolicyError("data queue profile cannot be loaded") from exc
    return parse_sfu_broadcast_data_queue_profile(document)


def parse_sfu_broadcast_data_queue_profile(
    document: object,
) -> SfuBroadcastDataQueueProfile:
    root = _require_object(document, "profile")
    _require_exact_keys(
        root,
        {
            "$schema",
            "profile_id",
            "profile_version",
            "payload_inspection",
            "accounting",
            "per_destination_class",
            "aggregate_limits",
            "traffic_kinds",
            "reason_codes",
            "cleanup",
        },
        "profile",
    )
    _require_string(root["$schema"], "$schema")
    profile_id = _require_string(root["profile_id"], "profile_id")
    profile_version = _require_string(root["profile_version"], "profile_version")
    if profile_version != "1.0.0":
        raise DataQueuePolicyError("profile_version must be 1.0.0")
    if root["payload_inspection"] != "forbidden":
        raise DataQueuePolicyError("payload_inspection must be forbidden")
    _parse_accounting(root["accounting"])

    class_document = _require_object(
        root["per_destination_class"], "per_destination_class"
    )
    _require_exact_keys(class_document, set(_ALLOWED_KINDS), "per_destination_class")
    class_profiles: dict[DataTrafficClass, TrafficClassProfile] = {}
    for traffic_class in DataTrafficClass:
        class_profiles[traffic_class] = _parse_class_profile(
            traffic_class,
            class_document[traffic_class.value],
        )
    priorities = {profile.priority for profile in class_profiles.values()}
    if priorities != set(range(len(DataTrafficClass))):
        raise DataQueuePolicyError("traffic class priorities must be unique and contiguous")
    actions = {profile.overflow_action for profile in class_profiles.values()}
    if actions != set(QueueOverflowAction):
        raise DataQueuePolicyError("profiles must exercise all bounded overflow actions")

    aggregate_document = _require_object(root["aggregate_limits"], "aggregate_limits")
    _require_exact_keys(
        aggregate_document,
        {"room", "browser_instance", "hub_send_data_adapter"},
        "aggregate_limits",
    )
    room_limits = _parse_aggregate_limits(aggregate_document["room"], "aggregate_limits.room")
    browser_limits = _parse_aggregate_limits(
        aggregate_document["browser_instance"],
        "aggregate_limits.browser_instance",
    )
    hub_limits = _parse_aggregate_limits(
        aggregate_document["hub_send_data_adapter"],
        "aggregate_limits.hub_send_data_adapter",
    )
    _validate_limit_hierarchy(class_profiles, room_limits, browser_limits, hub_limits)

    traffic_kinds = _parse_traffic_kinds(root["traffic_kinds"])
    reason_codes = _parse_reason_codes(root["reason_codes"])
    cleanup = _parse_cleanup(root["cleanup"])
    if cleanup.sweep_interval_ms >= min(
        profile.limits.age_ms_max for profile in class_profiles.values()
    ):
        raise DataQueuePolicyError("cleanup sweep must be shorter than every age cap")

    return SfuBroadcastDataQueueProfile(
        profile_id=profile_id,
        profile_version=profile_version,
        per_destination_class=MappingProxyType(class_profiles),
        room_limits=room_limits,
        browser_instance_limits=browser_limits,
        hub_send_data_adapter_limits=hub_limits,
        traffic_kinds=MappingProxyType(traffic_kinds),
        reason_codes=reason_codes,
        cleanup=cleanup,
    )


def _parse_accounting(value: object) -> None:
    document = _require_object(value, "accounting")
    expected = {
        "queue_bytes_unit": "encoded_wire_bytes_before_publish",
        "buffered_duration_unit": "milliseconds",
        "chunk_count_unit": "application_chunks_before_publish",
    }
    _require_exact_keys(document, set(expected), "accounting")
    if any(document[key] != expected_value for key, expected_value in expected.items()):
        raise DataQueuePolicyError("accounting units must use the v1 wire contract")


def _parse_class_profile(
    traffic_class: DataTrafficClass,
    value: object,
) -> TrafficClassProfile:
    path = f"per_destination_class.{traffic_class.value}"
    document = _require_object(value, path)
    _require_exact_keys(
        document,
        {
            "priority",
            "overflow_action",
            "overflow_reason_code",
            "coalesce_key_required",
            "limits",
        },
        path,
    )
    priority = _require_bounded_int(document["priority"], f"{path}.priority", 0, 4)
    try:
        overflow_action = QueueOverflowAction(document["overflow_action"])
    except (TypeError, ValueError) as exc:
        raise DataQueuePolicyError(f"{path}.overflow_action is invalid") from exc
    reason_code = _require_reason_code(
        document["overflow_reason_code"], f"{path}.overflow_reason_code"
    )
    coalesce_required = _require_bool(
        document["coalesce_key_required"], f"{path}.coalesce_key_required"
    )
    if coalesce_required != (overflow_action is QueueOverflowAction.COALESCE):
        raise DataQueuePolicyError(
            f"{path}.coalesce_key_required must match coalesce overflow action"
        )
    return TrafficClassProfile(
        traffic_class=traffic_class,
        priority=priority,
        overflow_action=overflow_action,
        overflow_reason_code=reason_code,
        coalesce_key_required=coalesce_required,
        limits=_parse_queue_limits(document["limits"], f"{path}.limits"),
    )


def _parse_queue_limits(value: object, path: str) -> QueueLimits:
    document = _require_object(value, path)
    fields = {*_LIMIT_FIELDS, "age_ms_max"}
    _require_exact_keys(document, fields, path)
    return QueueLimits(
        queue_bytes_max=_require_bounded_int(
            document["queue_bytes_max"], f"{path}.queue_bytes_max", 1, 8_388_608
        ),
        messages_max=_require_bounded_int(
            document["messages_max"], f"{path}.messages_max", 1, 4096
        ),
        age_ms_max=_require_bounded_int(
            document["age_ms_max"], f"{path}.age_ms_max", 1, 60_000
        ),
        buffered_duration_ms_max=_require_bounded_int(
            document["buffered_duration_ms_max"],
            f"{path}.buffered_duration_ms_max",
            1,
            600_000,
        ),
        chunk_count_max=_require_bounded_int(
            document["chunk_count_max"], f"{path}.chunk_count_max", 1, 8192
        ),
    )


def _parse_aggregate_limits(value: object, path: str) -> AggregateQueueLimits:
    document = _require_object(value, path)
    _require_exact_keys(document, set(_LIMIT_FIELDS), path)
    return AggregateQueueLimits(
        queue_bytes_max=_require_bounded_int(
            document["queue_bytes_max"], f"{path}.queue_bytes_max", 1, 16_777_216
        ),
        messages_max=_require_bounded_int(
            document["messages_max"], f"{path}.messages_max", 1, 8192
        ),
        buffered_duration_ms_max=_require_bounded_int(
            document["buffered_duration_ms_max"],
            f"{path}.buffered_duration_ms_max",
            1,
            1_200_000,
        ),
        chunk_count_max=_require_bounded_int(
            document["chunk_count_max"], f"{path}.chunk_count_max", 1, 16_384
        ),
    )


def _parse_traffic_kinds(value: object) -> dict[str, TrafficKindRule]:
    if not isinstance(value, list):
        raise DataQueuePolicyError("traffic_kinds must be a JSON array")
    rules: dict[str, TrafficKindRule] = {}
    for index, raw_rule in enumerate(value):
        path = f"traffic_kinds[{index}]"
        document = _require_object(raw_rule, path)
        _require_exact_keys(
            document,
            {
                "traffic_kind",
                "traffic_class",
                "authority",
                "delivery_scope",
                "allowed",
                "reason_code",
            },
            path,
        )
        kind = _require_string(document["traffic_kind"], f"{path}.traffic_kind")
        if kind in rules:
            raise DataQueuePolicyError(f"duplicate traffic kind: {kind}")
        try:
            authority = TrafficAuthority(document["authority"])
            delivery_scope = DeliveryScope(document["delivery_scope"])
        except (TypeError, ValueError) as exc:
            raise DataQueuePolicyError(f"{path} has an invalid closed enum") from exc
        allowed = _require_bool(document["allowed"], f"{path}.allowed")
        raw_class = document["traffic_class"]
        if raw_class is None:
            traffic_class = None
        else:
            try:
                traffic_class = DataTrafficClass(raw_class)
            except (TypeError, ValueError) as exc:
                raise DataQueuePolicyError(f"{path}.traffic_class is invalid") from exc
        rule = TrafficKindRule(
            traffic_kind=kind,
            traffic_class=traffic_class,
            authority=authority,
            delivery_scope=delivery_scope,
            allowed=allowed,
            reason_code=_require_reason_code(document["reason_code"], f"{path}.reason_code"),
        )
        _validate_traffic_rule(rule, path)
        rules[kind] = rule
    if set(rules) != _ALLOWED_KINDS | _FORBIDDEN_KINDS:
        raise DataQueuePolicyError("traffic_kinds must contain the closed v1 kind set")
    return rules


def _validate_traffic_rule(rule: TrafficKindRule, path: str) -> None:
    if rule.traffic_kind in _ALLOWED_KINDS:
        if (
            not rule.allowed
            or rule.traffic_class is None
            or rule.traffic_class.value != rule.traffic_kind
            or rule.authority is not TrafficAuthority.NON_AUTHORITATIVE
            or rule.delivery_scope is DeliveryScope.FORBIDDEN
        ):
            raise DataQueuePolicyError(f"{path} weakens an allowed traffic contract")
        return
    expected_authority = (
        TrafficAuthority.AUTHORITATIVE
        if rule.traffic_kind in _AUTHORITATIVE_KINDS
        else TrafficAuthority.PROHIBITED_PAYLOAD
    )
    if (
        rule.allowed
        or rule.traffic_class is not None
        or rule.authority is not expected_authority
        or rule.delivery_scope is not DeliveryScope.FORBIDDEN
    ):
        raise DataQueuePolicyError(f"{path} must fail closed")


def _parse_reason_codes(value: object) -> QueueReasonCodes:
    document = _require_object(value, "reason_codes")
    fields = {
        "accepted",
        "accepted_after_priority_eviction",
        "coalesced",
        "unknown_traffic_kind",
        "forbidden_traffic_kind",
        "invalid_metadata",
        "blocked_timeout",
        "cleanup_expired",
    }
    _require_exact_keys(document, fields, "reason_codes")
    values = {
        field: _require_reason_code(document[field], f"reason_codes.{field}")
        for field in fields
    }
    if len(set(values.values())) != len(values):
        raise DataQueuePolicyError("reason_codes must be unique")
    return QueueReasonCodes(**values)


def _parse_cleanup(value: object) -> QueueCleanupProfile:
    document = _require_object(value, "cleanup")
    _require_exact_keys(
        document,
        {"sweep_interval_ms", "disconnect_after_blocked_ms"},
        "cleanup",
    )
    return QueueCleanupProfile(
        sweep_interval_ms=_require_bounded_int(
            document["sweep_interval_ms"], "cleanup.sweep_interval_ms", 10, 1000
        ),
        disconnect_after_blocked_ms=_require_bounded_int(
            document["disconnect_after_blocked_ms"],
            "cleanup.disconnect_after_blocked_ms",
            1000,
            60_000,
        ),
    )


def _validate_limit_hierarchy(
    classes: Mapping[DataTrafficClass, TrafficClassProfile],
    room: AggregateQueueLimits,
    browser: AggregateQueueLimits,
    hub: AggregateQueueLimits,
) -> None:
    for profile in classes.values():
        for field in _LIMIT_FIELDS:
            if getattr(profile.limits, field) > getattr(room, field):
                raise DataQueuePolicyError(
                    f"{profile.traffic_class.value}.{field} exceeds room limit"
                )
    for adapter_name, adapter in (("browser", browser), ("hub", hub)):
        for field in _LIMIT_FIELDS:
            if getattr(room, field) > getattr(adapter, field):
                raise DataQueuePolicyError(f"room.{field} exceeds {adapter_name} limit")


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


def _require_object(value: object, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise DataQueuePolicyError(f"{path} must be a JSON object")
    return value


def _require_exact_keys(
    value: Mapping[str, Any], expected: set[str], path: str
) -> None:
    actual = set(value)
    if actual != expected:
        raise DataQueuePolicyError(
            f"{path} has invalid fields; "
            f"missing={sorted(expected - actual)}, "
            f"unexpected={sorted(str(key) for key in actual - expected)}"
        )


def _require_string(value: object, path: str) -> str:
    if not _is_handle(value):
        raise DataQueuePolicyError(f"{path} must be a non-empty trimmed string")
    return value


def _require_reason_code(value: object, path: str) -> str:
    code = _require_string(value, path)
    if _REASON_CODE.fullmatch(code) is None:
        raise DataQueuePolicyError(f"{path} must be a content-free reason code")
    return code


def _require_bool(value: object, path: str) -> bool:
    if not isinstance(value, bool):
        raise DataQueuePolicyError(f"{path} must be a boolean")
    return value


def _require_bounded_int(
    value: object,
    path: str,
    minimum: int,
    maximum: int,
) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
        raise DataQueuePolicyError(
            f"{path} must be an integer between {minimum} and {maximum}"
        )
    return value


def _is_handle(value: object) -> bool:
    return isinstance(value, str) and bool(value) and value == value.strip()


def _is_positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _is_non_negative_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0
