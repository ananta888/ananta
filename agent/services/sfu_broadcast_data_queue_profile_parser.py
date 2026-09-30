"""Strict loader and parser for SFU broadcast data queue profiles.

The parser validates the complete profile document before any queue ledger
is constructed; it never touches message content.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Any

from agent.services.sfu_broadcast_data_queue_models import (
    AggregateQueueLimits,
    DataQueuePolicyError,
    DataTrafficClass,
    DeliveryScope,
    _is_handle,
    QueueCleanupProfile,
    QueueLimits,
    QueueOverflowAction,
    QueueReasonCodes,
    SfuBroadcastDataQueueProfile,
    TrafficAuthority,
    TrafficClassProfile,
    TrafficKindRule,
)


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


__all__ = [
    "load_sfu_broadcast_data_queue_profile",
    "parse_sfu_broadcast_data_queue_profile",
]
