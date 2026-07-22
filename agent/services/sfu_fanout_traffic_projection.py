"""Fail-closed projection of parent DataChannel kinds onto SFU route classes.

The service deliberately accepts contract metadata only. It never receives or
inspects a message payload and performs no routing side effects.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any


class TrafficProjectionPolicyError(ValueError):
    """Raised when a traffic projection policy is malformed or unsafe."""


class SfuFanoutRouteClass(str, Enum):
    """Closed set of SFU routing classes."""

    SHARED = "shared"
    RECEIVER_PRIVATE = "receiver_private"
    PAIR_PRIVATE = "pair_private"
    FORBIDDEN_FOR_SFU = "forbidden_for_sfu"


class SfuTrafficPrivacyScope(str, Enum):
    """Privacy scope required by a route class."""

    AUTHORIZED_GROUP = "authorized_group"
    AUTHORIZED_RECEIVER = "authorized_receiver"
    AUTHORIZED_SENDER_RECEIVER_PAIR = "authorized_sender_receiver_pair"
    SFU_FORBIDDEN = "sfu_forbidden"


_PRIVACY_SCOPE_BY_ROUTE_CLASS = {
    SfuFanoutRouteClass.SHARED: SfuTrafficPrivacyScope.AUTHORIZED_GROUP,
    SfuFanoutRouteClass.RECEIVER_PRIVATE: SfuTrafficPrivacyScope.AUTHORIZED_RECEIVER,
    SfuFanoutRouteClass.PAIR_PRIVATE: (
        SfuTrafficPrivacyScope.AUTHORIZED_SENDER_RECEIVER_PAIR
    ),
    SfuFanoutRouteClass.FORBIDDEN_FOR_SFU: SfuTrafficPrivacyScope.SFU_FORBIDDEN,
}


@dataclass(frozen=True, slots=True)
class ParentMessageContract:
    schema_id: str
    schema_version: str
    message_kind_field: str


@dataclass(frozen=True, slots=True)
class TrafficProjectionRule:
    parent_message_kind: str
    parent_schema_version: str
    privacy_scope: SfuTrafficPrivacyScope
    route_class: SfuFanoutRouteClass
    reason_code: str


@dataclass(frozen=True, slots=True)
class FailClosedProjectionDefaults:
    route_class: SfuFanoutRouteClass
    privacy_scope: SfuTrafficPrivacyScope
    unknown_kind_reason_code: str
    unsupported_schema_reason_code: str


@dataclass(frozen=True, slots=True)
class SfuFanoutTrafficProjectionPolicy:
    policy_id: str
    policy_version: str
    parent_contract: ParentMessageContract
    payload_inspection: str
    fail_closed_defaults: FailClosedProjectionDefaults
    classifications: tuple[TrafficProjectionRule, ...]

    @property
    def known_message_kinds(self) -> frozenset[str]:
        return frozenset(rule.parent_message_kind for rule in self.classifications)


@dataclass(frozen=True, slots=True)
class TrafficProjectionDecision:
    policy_id: str
    policy_version: str
    parent_message_kind: str | None
    parent_schema_version: str | None
    privacy_scope: SfuTrafficPrivacyScope
    route_class: SfuFanoutRouteClass
    reason_code: str
    matched_policy_rule: bool

    @property
    def is_group_broadcast(self) -> bool:
        return self.route_class is SfuFanoutRouteClass.SHARED

    @property
    def permits_sfu_route(self) -> bool:
        return self.route_class is not SfuFanoutRouteClass.FORBIDDEN_FOR_SFU


class SfuFanoutTrafficProjectionService:
    """Pure lookup service for the hub-owned traffic projection policy."""

    __slots__ = ("_classifications", "_policy")

    def __init__(self, policy: SfuFanoutTrafficProjectionPolicy) -> None:
        self._policy = policy
        self._classifications: Mapping[str, TrafficProjectionRule] = MappingProxyType(
            {rule.parent_message_kind: rule for rule in policy.classifications}
        )

    @property
    def policy(self) -> SfuFanoutTrafficProjectionPolicy:
        return self._policy

    def classify(
        self,
        *,
        parent_message_kind: object,
        parent_schema_version: object,
    ) -> TrafficProjectionDecision:
        """Classify exact contract identifiers without parsing message content."""

        kind = parent_message_kind if isinstance(parent_message_kind, str) else None
        schema_version = (
            parent_schema_version if isinstance(parent_schema_version, str) else None
        )

        if schema_version != self._policy.parent_contract.schema_version:
            return self._fail_closed_decision(
                parent_message_kind=kind,
                parent_schema_version=schema_version,
                reason_code=(
                    self._policy.fail_closed_defaults.unsupported_schema_reason_code
                ),
            )

        rule = self._classifications.get(kind) if kind is not None else None
        if rule is None:
            return self._fail_closed_decision(
                parent_message_kind=kind,
                parent_schema_version=schema_version,
                reason_code=self._policy.fail_closed_defaults.unknown_kind_reason_code,
            )

        return TrafficProjectionDecision(
            policy_id=self._policy.policy_id,
            policy_version=self._policy.policy_version,
            parent_message_kind=rule.parent_message_kind,
            parent_schema_version=rule.parent_schema_version,
            privacy_scope=rule.privacy_scope,
            route_class=rule.route_class,
            reason_code=rule.reason_code,
            matched_policy_rule=True,
        )

    def _fail_closed_decision(
        self,
        *,
        parent_message_kind: str | None,
        parent_schema_version: str | None,
        reason_code: str,
    ) -> TrafficProjectionDecision:
        defaults = self._policy.fail_closed_defaults
        return TrafficProjectionDecision(
            policy_id=self._policy.policy_id,
            policy_version=self._policy.policy_version,
            parent_message_kind=parent_message_kind,
            parent_schema_version=parent_schema_version,
            privacy_scope=defaults.privacy_scope,
            route_class=defaults.route_class,
            reason_code=reason_code,
            matched_policy_rule=False,
        )


def load_sfu_fanout_traffic_projection_policy(
    path: str | Path,
) -> SfuFanoutTrafficProjectionPolicy:
    """Load and validate a projection policy from a JSON infrastructure boundary."""

    policy_path = Path(path)
    try:
        with policy_path.open("r", encoding="utf-8") as handle:
            document = json.load(handle)
    except json.JSONDecodeError as exc:
        raise TrafficProjectionPolicyError(
            f"traffic projection policy is not valid JSON: {exc.msg}"
        ) from exc
    return parse_sfu_fanout_traffic_projection_policy(document)


def parse_sfu_fanout_traffic_projection_policy(
    document: object,
) -> SfuFanoutTrafficProjectionPolicy:
    """Validate an untrusted policy document into an immutable domain model."""

    root = _require_object(document, "policy")
    _require_exact_keys(
        root,
        {
            "policy_id",
            "policy_version",
            "parent_contract",
            "payload_inspection",
            "fail_closed_defaults",
            "classifications",
        },
        "policy",
    )

    parent_document = _require_object(root["parent_contract"], "parent_contract")
    _require_exact_keys(
        parent_document,
        {"schema_id", "schema_version", "message_kind_field"},
        "parent_contract",
    )
    parent_contract = ParentMessageContract(
        schema_id=_require_string(parent_document["schema_id"], "parent_contract.schema_id"),
        schema_version=_require_string(
            parent_document["schema_version"], "parent_contract.schema_version"
        ),
        message_kind_field=_require_string(
            parent_document["message_kind_field"],
            "parent_contract.message_kind_field",
        ),
    )

    payload_inspection = _require_string(
        root["payload_inspection"], "payload_inspection"
    )
    if payload_inspection != "forbidden":
        raise TrafficProjectionPolicyError(
            "payload_inspection must be 'forbidden' for the projection service"
        )

    defaults_document = _require_object(
        root["fail_closed_defaults"], "fail_closed_defaults"
    )
    _require_exact_keys(
        defaults_document,
        {
            "route_class",
            "privacy_scope",
            "unknown_kind_reason_code",
            "unsupported_schema_reason_code",
        },
        "fail_closed_defaults",
    )
    defaults = FailClosedProjectionDefaults(
        route_class=_parse_route_class(
            defaults_document["route_class"], "fail_closed_defaults.route_class"
        ),
        privacy_scope=_parse_privacy_scope(
            defaults_document["privacy_scope"],
            "fail_closed_defaults.privacy_scope",
        ),
        unknown_kind_reason_code=_require_string(
            defaults_document["unknown_kind_reason_code"],
            "fail_closed_defaults.unknown_kind_reason_code",
        ),
        unsupported_schema_reason_code=_require_string(
            defaults_document["unsupported_schema_reason_code"],
            "fail_closed_defaults.unsupported_schema_reason_code",
        ),
    )
    if (
        defaults.route_class is not SfuFanoutRouteClass.FORBIDDEN_FOR_SFU
        or defaults.privacy_scope is not SfuTrafficPrivacyScope.SFU_FORBIDDEN
    ):
        raise TrafficProjectionPolicyError(
            "fail_closed_defaults must use forbidden_for_sfu and sfu_forbidden"
        )

    classifications_document = root["classifications"]
    if not isinstance(classifications_document, list) or not classifications_document:
        raise TrafficProjectionPolicyError(
            "classifications must be a non-empty JSON array"
        )

    classifications: list[TrafficProjectionRule] = []
    seen_message_kinds: set[str] = set()
    for index, raw_rule in enumerate(classifications_document):
        rule_path = f"classifications[{index}]"
        rule_document = _require_object(raw_rule, rule_path)
        _require_exact_keys(
            rule_document,
            {
                "parent_message_kind",
                "parent_schema_version",
                "privacy_scope",
                "route_class",
                "reason_code",
            },
            rule_path,
        )
        rule = TrafficProjectionRule(
            parent_message_kind=_require_string(
                rule_document["parent_message_kind"],
                f"{rule_path}.parent_message_kind",
            ),
            parent_schema_version=_require_string(
                rule_document["parent_schema_version"],
                f"{rule_path}.parent_schema_version",
            ),
            privacy_scope=_parse_privacy_scope(
                rule_document["privacy_scope"], f"{rule_path}.privacy_scope"
            ),
            route_class=_parse_route_class(
                rule_document["route_class"], f"{rule_path}.route_class"
            ),
            reason_code=_require_string(
                rule_document["reason_code"], f"{rule_path}.reason_code"
            ),
        )
        if rule.parent_message_kind in seen_message_kinds:
            raise TrafficProjectionPolicyError(
                f"duplicate parent message kind: {rule.parent_message_kind}"
            )
        if rule.parent_schema_version != parent_contract.schema_version:
            raise TrafficProjectionPolicyError(
                f"{rule_path}.parent_schema_version does not match parent contract"
            )
        required_scope = _PRIVACY_SCOPE_BY_ROUTE_CLASS[rule.route_class]
        if rule.privacy_scope is not required_scope:
            raise TrafficProjectionPolicyError(
                f"{rule_path}.privacy_scope does not match its route class"
            )
        seen_message_kinds.add(rule.parent_message_kind)
        classifications.append(rule)

    represented_classes = {rule.route_class for rule in classifications}
    if represented_classes != set(SfuFanoutRouteClass):
        raise TrafficProjectionPolicyError(
            "classifications must represent all four closed route classes"
        )

    return SfuFanoutTrafficProjectionPolicy(
        policy_id=_require_string(root["policy_id"], "policy_id"),
        policy_version=_require_string(root["policy_version"], "policy_version"),
        parent_contract=parent_contract,
        payload_inspection=payload_inspection,
        fail_closed_defaults=defaults,
        classifications=tuple(classifications),
    )


def _require_object(value: object, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TrafficProjectionPolicyError(f"{path} must be a JSON object")
    return value


def _require_exact_keys(
    value: Mapping[str, Any], expected: set[str], path: str
) -> None:
    actual = set(value)
    if actual == expected:
        return
    missing = sorted(expected - actual)
    unexpected = sorted(str(key) for key in actual - expected)
    raise TrafficProjectionPolicyError(
        f"{path} has invalid fields; missing={missing}, unexpected={unexpected}"
    )


def _require_string(value: object, path: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise TrafficProjectionPolicyError(
            f"{path} must be a non-empty string without surrounding whitespace"
        )
    return value


def _parse_route_class(value: object, path: str) -> SfuFanoutRouteClass:
    raw_value = _require_string(value, path)
    try:
        return SfuFanoutRouteClass(raw_value)
    except ValueError as exc:
        raise TrafficProjectionPolicyError(f"{path} is not a known route class") from exc


def _parse_privacy_scope(value: object, path: str) -> SfuTrafficPrivacyScope:
    raw_value = _require_string(value, path)
    try:
        return SfuTrafficPrivacyScope(raw_value)
    except ValueError as exc:
        raise TrafficProjectionPolicyError(f"{path} is not a known privacy scope") from exc
