from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from agent.services.sfu_broadcast_data_queue_policy import (
    BoundedSfuBroadcastDataQueue,
    DataQueuePolicyError,
    DataTrafficClass,
    QueueAdapterKind,
    QueueLimitScope,
    QueueOffer,
    QueueOverflowAction,
    load_sfu_broadcast_data_queue_profile,
    parse_sfu_broadcast_data_queue_profile,
)


ROOT = Path(__file__).resolve().parents[2]
PROFILE_PATH = ROOT / "config" / "sfu_broadcast_egress_limits.json"
SCHEMA_PATH = ROOT / "schemas" / "webrtc" / "sfu_broadcast_data_queue_profile.v1.json"
TS_PORT_PATH = (
    ROOT
    / "frontend-angular"
    / "src"
    / "app"
    / "services"
    / "sfu-broadcast-data-queue.port.ts"
)


@pytest.fixture()
def profile():
    return load_sfu_broadcast_data_queue_profile(PROFILE_PATH)


def _offer(
    sequence: int,
    *,
    kind: str = "shared_reference",
    room: str = "room-1",
    destination: str | None = None,
    queue_bytes: int = 1,
    duration_ms: int = 0,
    chunks: int = 1,
    enqueued_at_ms: int = 0,
    coalesce_key: str | None = None,
) -> QueueOffer:
    return QueueOffer(
        message_id=f"message-{sequence:05d}",
        room_handle=room,
        destination_handle=destination or f"destination-{sequence:05d}",
        traffic_kind=kind,
        queue_bytes=queue_bytes,
        buffered_duration_ms=duration_ms,
        chunk_count=chunks,
        enqueued_at_ms=enqueued_at_ms,
        coalesce_key=coalesce_key,
    )


def _queue(profile, kind: QueueAdapterKind) -> BoundedSfuBroadcastDataQueue:
    return BoundedSfuBroadcastDataQueue(
        profile,
        adapter_kind=kind,
        adapter_handle=f"{kind.value}-1",
    )


def test_profile_conforms_to_schema_and_closed_policy(profile) -> None:
    jsonschema = pytest.importorskip("jsonschema")
    document = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator(schema).validate(document)

    assert set(profile.per_destination_class) == set(DataTrafficClass)
    assert {item.overflow_action for item in profile.per_destination_class.values()} == set(
        QueueOverflowAction
    )
    assert all("payload" not in field for field in profile.reason_codes.__dataclass_fields__)


def test_typescript_port_and_python_policy_share_exact_bounded_fields() -> None:
    config = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    source = TS_PORT_PATH.read_text(encoding="utf-8")
    match = re.search(
        r"SFU_BROADCAST_DATA_QUEUE_LIMITS_CONTRACT_JSON\s*=\s*\n\s*'([^']+)' as const;",
        source,
    )
    assert match is not None
    browser_contract = json.loads(match.group(1))
    assert browser_contract == {
        "per_destination_class": config["per_destination_class"],
        "aggregate_limits": config["aggregate_limits"],
        "cleanup": config["cleanup"],
    }
    assert "payload:" not in source
    assert "payloadBytes" not in source


@pytest.mark.parametrize(
    ("adapter_kind", "message_cap"),
    [
        (QueueAdapterKind.BROWSER_INSTANCE, 512),
        (QueueAdapterKind.HUB_SEND_DATA_ADAPTER, 2048),
    ],
)
def test_burst_is_bounded_per_browser_and_hub_adapter(
    profile,
    adapter_kind: QueueAdapterKind,
    message_cap: int,
) -> None:
    queue = _queue(profile, adapter_kind)
    for sequence in range(message_cap):
        decision = queue.enqueue(
            _offer(sequence, room=f"room-{sequence // 250}"),
            now_ms=0,
        )
        assert decision.accepted

    rejected = queue.enqueue(
        _offer(message_cap, room=f"room-{message_cap // 250}"),
        now_ms=0,
    )
    assert not rejected.accepted
    assert rejected.overflow_action is QueueOverflowAction.LAYER_DOWNSHIFT
    assert rejected.limit_scope.value == adapter_kind.value
    assert queue.snapshot().usage.messages == message_cap


def test_room_budget_is_enforced_below_adapter_budget(profile) -> None:
    queue = _queue(profile, QueueAdapterKind.BROWSER_INSTANCE)
    for sequence in range(profile.room_limits.messages_max):
        assert queue.enqueue(_offer(sequence), now_ms=0).accepted

    decision = queue.enqueue(_offer(1000), now_ms=0)
    assert not decision.accepted
    assert decision.limit_scope is QueueLimitScope.ROOM


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("queue_bytes", 131073),
        ("buffered_duration_ms", 8001),
        ("chunk_count", 129),
    ],
)
def test_single_oversize_offer_uses_class_action(profile, field: str, value: int) -> None:
    queue = _queue(profile, QueueAdapterKind.BROWSER_INSTANCE)
    kwargs = {field: value}
    offer = _offer(
        1,
        queue_bytes=kwargs.get("queue_bytes", 1),
        duration_ms=kwargs.get("buffered_duration_ms", 0),
        chunks=kwargs.get("chunk_count", 1),
    )
    decision = queue.enqueue(offer, now_ms=0)
    assert not decision.accepted
    assert decision.limit_scope is QueueLimitScope.DESTINATION_CLASS
    assert decision.overflow_action is QueueOverflowAction.LAYER_DOWNSHIFT
    assert queue.snapshot().usage.messages == 0


def test_age_cap_rejects_stale_offer_and_timer_cleanup_reclaims_entries(profile) -> None:
    queue = _queue(profile, QueueAdapterKind.BROWSER_INSTANCE)
    stale = queue.enqueue(_offer(1, enqueued_at_ms=0), now_ms=5001)
    assert not stale.accepted
    assert stale.limit_scope is QueueLimitScope.AGE

    fresh = queue.enqueue(_offer(2, enqueued_at_ms=10), now_ms=10)
    assert fresh.accepted
    cleanup = queue.cleanup(now_ms=5011)
    assert cleanup.removed_message_ids == ("message-00002",)
    assert not cleanup.disconnect_required
    assert queue.snapshot().usage.messages == 0


def test_coalesce_replaces_only_same_destination_class_and_key(profile) -> None:
    queue = _queue(profile, QueueAdapterKind.BROWSER_INSTANCE)
    first = _offer(
        1,
        kind="transcript_revision",
        destination="receiver-1",
        coalesce_key="segment-7",
    )
    replacement = _offer(
        2,
        kind="transcript_revision",
        destination="receiver-1",
        coalesce_key="segment-7",
    )
    assert queue.enqueue(first, now_ms=0).accepted
    decision = queue.enqueue(replacement, now_ms=0)
    assert decision.accepted
    assert decision.overflow_action is QueueOverflowAction.COALESCE
    assert decision.removed_message_ids == ("message-00001",)
    assert tuple(entry.message_id for entry in queue.snapshot().entries) == (
        "message-00002",
    )


def test_higher_priority_interrupt_preempts_oldest_low_priority_entry(profile) -> None:
    queue = _queue(profile, QueueAdapterKind.BROWSER_INSTANCE)
    for sequence in range(profile.room_limits.messages_max):
        assert queue.enqueue(_offer(sequence), now_ms=0).accepted

    interrupt = _offer(
        999,
        kind="interrupt",
        destination="receiver-priority",
        coalesce_key="stream-1",
    )
    decision = queue.enqueue(interrupt, now_ms=0)
    assert decision.accepted
    assert decision.overflow_action is QueueOverflowAction.DROP
    assert decision.removed_message_ids == ("message-00000",)
    assert queue.snapshot().usage.messages == profile.room_limits.messages_max


def test_sustained_blocked_adapter_disconnects_and_releases_all_metadata(profile) -> None:
    queue = _queue(profile, QueueAdapterKind.HUB_SEND_DATA_ADAPTER)
    assert queue.enqueue(_offer(1), now_ms=0).accepted
    queue.mark_blocked(now_ms=10)

    before_timeout = queue.cleanup(
        now_ms=10 + profile.cleanup.disconnect_after_blocked_ms - 1
    )
    assert not before_timeout.disconnect_required
    assert before_timeout.removed_message_ids == ("message-00001",)
    at_timeout = queue.cleanup(
        now_ms=10 + profile.cleanup.disconnect_after_blocked_ms
    )
    assert at_timeout.disconnect_required
    assert at_timeout.removed_message_ids == ()
    snapshot = queue.snapshot()
    assert snapshot.disconnected
    assert snapshot.blocked_since_ms is None
    assert snapshot.usage.messages == 0

    rejected = queue.enqueue(
        _offer(2),
        now_ms=10 + profile.cleanup.disconnect_after_blocked_ms,
    )
    assert not rejected.accepted
    assert rejected.overflow_action is QueueOverflowAction.DISCONNECT
    assert rejected.limit_scope is QueueLimitScope.BLOCKED_TIMEOUT


@pytest.mark.parametrize(
    "traffic_kind",
    [
        "authoritative_membership_mutation",
        "authoritative_key_mutation",
        "training_payload",
        "dataset_payload",
        "model_adapter_payload",
        "evidence_payload",
        "unknown_future_kind",
    ],
)
def test_authoritative_sensitive_and_unknown_traffic_fail_closed(
    profile,
    traffic_kind: str,
) -> None:
    queue = _queue(profile, QueueAdapterKind.BROWSER_INSTANCE)
    decision = queue.enqueue(_offer(1, kind=traffic_kind), now_ms=0)
    assert not decision.accepted
    assert decision.overflow_action is None
    assert queue.snapshot().usage.messages == 0


def test_parser_rejects_attempt_to_allow_authoritative_membership(profile) -> None:
    document = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    membership = next(
        rule
        for rule in document["traffic_kinds"]
        if rule["traffic_kind"] == "authoritative_membership_mutation"
    )
    membership.update(
        {
            "allowed": True,
            "traffic_class": "control_hint",
            "authority": "non_authoritative",
            "delivery_scope": "authorized_group",
        }
    )
    with pytest.raises(DataQueuePolicyError, match="fail closed"):
        parse_sfu_broadcast_data_queue_profile(document)


def test_reason_codes_are_content_free(profile) -> None:
    codes = [
        value
        for value in profile.reason_codes.__dict__.values()
        if isinstance(value, str)
    ] if hasattr(profile.reason_codes, "__dict__") else [
        profile.reason_codes.accepted,
        profile.reason_codes.accepted_after_priority_eviction,
        profile.reason_codes.coalesced,
        profile.reason_codes.unknown_traffic_kind,
        profile.reason_codes.forbidden_traffic_kind,
        profile.reason_codes.invalid_metadata,
        profile.reason_codes.blocked_timeout,
        profile.reason_codes.cleanup_expired,
    ]
    codes.extend(
        item.overflow_reason_code
        for item in profile.per_destination_class.values()
    )
    assert all(re.fullmatch(r"[A-Z][A-Z0-9_]{2,95}", code) for code in codes)
    assert all("{" not in code and "/" not in code and ":" not in code for code in codes)
