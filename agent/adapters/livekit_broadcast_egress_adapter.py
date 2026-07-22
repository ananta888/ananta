"""Capability-gated LiveKit subscription and content-free egress boundary."""

from __future__ import annotations

import hashlib
import json
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Callable, Literal, Mapping, Protocol

from agent.services.sfu_broadcast_runtime_control_port import (
    SfuBroadcastRuntimeControlPort,
    SfuRuntimeControlCommand,
)


SubscriptionOperation = Literal["subscribe", "update", "unsubscribe"]
RuntimeAction = Literal["queue", "scheduler", "downshift", "disconnect", "keyframe"]
_RUNTIME_CAPABILITY = {
    "queue": "runtime_queue_control",
    "scheduler": "runtime_egress_scheduler",
    "downshift": "runtime_egress_downshift",
    "disconnect": "runtime_disconnect",
    "keyframe": "runtime_keyframe_request",
}
_OBSERVATION_KEYS = frozenset({
    "tenant_id", "room_id", "publication_id", "node_id",
    "window_started_at_ms", "window_ended_at_ms", "route_epoch",
    "topology_epoch", "fencing_token", "actual_egress_bytes",
    "estimated_egress_bytes", "observable_drops", "receiver_count",
})


class LiveKitEgressControlResultPort(Protocol):
    accepted_by_api: bool
    authoritative_runtime_ack: bool
    reason_code: str
    calls_completed: int
    retryable: bool


class LiveKitEgressControlClientPort(Protocol):
    def capabilities(self) -> Mapping[str, object]: ...
    def apply(self, command: object) -> LiveKitEgressControlResultPort: ...
    def update(self, command: object) -> LiveKitEgressControlResultPort: ...
    def revoke(self, command: object) -> LiveKitEgressControlResultPort: ...


class SfuEgressCommandAuthorizationPort(Protocol):
    def authorize_subscription(self, command: "SfuEgressSubscriptionCommand") -> bool: ...
    def authorize_runtime_action(self, command: "SfuEgressRuntimeActionCommand") -> bool: ...


class SfuEgressRuntimeCapabilityPort(Protocol):
    def capabilities(self, target_runtime_id: str) -> Mapping[str, object]: ...


class SfuEgressObservationSourcePort(Protocol):
    def observe(self, query: "SfuEgressObservationQuery") -> Mapping[str, object] | None: ...


@dataclass(frozen=True, slots=True)
class SfuEgressSubscriptionCommand:
    operation_id: str
    operation: SubscriptionOperation
    tenant_id: str
    room_id: str
    publication_id: str
    route_epoch: int
    topology_epoch: int
    fencing_token: int
    issued_at_ms: int
    expires_at_ms: int
    route_command_digest: str
    fairness_profile_digest: str
    route_command: object


@dataclass(frozen=True, slots=True)
class SfuEgressRuntimeActionCommand:
    operation_id: str
    action: RuntimeAction
    tenant_id: str
    room_id: str
    publication_id: str
    route_epoch: int
    topology_epoch: int
    fencing_token: int
    expires_at_ms: int
    fairness_profile_digest: str
    control_command: SfuRuntimeControlCommand


@dataclass(frozen=True, slots=True)
class SfuEgressOperationResult:
    operation_id: str
    outcome: Literal["applied", "accepted_unverified", "rejected", "unknown"]
    reason_code: str
    calls_completed: int
    duplicate: bool = False
    retryable: bool = False


@dataclass(frozen=True, slots=True)
class SfuEgressObservationQuery:
    tenant_id: str
    room_id: str
    publication_id: str
    node_id: str
    route_epoch: int
    topology_epoch: int
    fencing_token: int


@dataclass(frozen=True, slots=True)
class SfuEgressObservationResult:
    available: bool
    reason_code: str
    metrics: Mapping[str, object]


class LivekitBroadcastEgressAdapter:
    """Delegates only proven APIs; it never owns browser layer selection."""

    def __init__(self, *, client: LiveKitEgressControlClientPort,
                 authorization: SfuEgressCommandAuthorizationPort,
                 runtime_capabilities: SfuEgressRuntimeCapabilityPort,
                 runtime_control: SfuBroadcastRuntimeControlPort | None = None,
                 observations: SfuEgressObservationSourcePort | None = None,
                 receipt_limit: int = 256,
                 clock: Callable[[], float] = time.time) -> None:
        if not 1 <= receipt_limit <= 4096:
            raise ValueError("sfu_egress_receipt_limit_invalid")
        self._client = client
        self._authorization = authorization
        self._runtime_capabilities = runtime_capabilities
        self._runtime_control = runtime_control
        self._observations = observations
        self._receipt_limit = receipt_limit
        self._clock = clock
        self._receipts: OrderedDict[str, tuple[str, SfuEgressOperationResult]] = OrderedDict()

    def mutate_subscription(self, command: SfuEgressSubscriptionCommand) -> SfuEgressOperationResult:
        failure = _validate_subscription(command, int(self._clock() * 1000))
        if failure:
            return _result(command.operation_id, "rejected", failure)
        digest = _subscription_digest(command)
        duplicate = self._duplicate(command.operation_id, digest)
        if duplicate is not None:
            return duplicate
        if not self._authorization.authorize_subscription(command):
            return self._remember(command.operation_id, digest, _result(
                command.operation_id, "rejected", "sfu_egress_authorization_denied",
            ))
        capability_name = {
            "subscribe": "route_apply", "update": "route_update", "unsubscribe": "route_revoke",
        }[command.operation]
        try:
            capabilities = self._client.capabilities()
        except Exception:
            return _result(command.operation_id, "unknown", "sfu_egress_runtime_unavailable", retryable=True)
        if capabilities.get(capability_name) not in {"accepted_unverified", "available"}:
            return self._remember(command.operation_id, digest, _result(
                command.operation_id, "rejected", "sfu_egress_capability_unsupported",
            ))
        try:
            if command.operation == "subscribe":
                control = self._client.apply(command.route_command)
            elif command.operation == "update":
                control = self._client.update(command.route_command)
            else:
                control = self._client.revoke(command.route_command)
        except Exception:
            return _result(command.operation_id, "unknown", "sfu_egress_runtime_unavailable", retryable=True)
        if control.accepted_by_api and control.authoritative_runtime_ack:
            outcome = "applied"
        elif control.accepted_by_api:
            outcome = "accepted_unverified"
        else:
            outcome = "rejected"
        result = SfuEgressOperationResult(
            command.operation_id, outcome, str(control.reason_code),
            max(0, int(control.calls_completed)), retryable=bool(control.retryable),
        )
        return self._remember(command.operation_id, digest, result)

    def execute_runtime_action(self, command: SfuEgressRuntimeActionCommand) -> SfuEgressOperationResult:
        failure = _validate_runtime(command, int(self._clock() * 1000))
        if failure:
            return _result(command.operation_id, "rejected", failure)
        digest = _runtime_digest(command)
        duplicate = self._duplicate(command.operation_id, digest)
        if duplicate is not None:
            return duplicate
        if not self._authorization.authorize_runtime_action(command):
            return self._remember(command.operation_id, digest, _result(
                command.operation_id, "rejected", "sfu_egress_authorization_denied",
            ))
        capability = _RUNTIME_CAPABILITY[command.action]
        try:
            available = self._runtime_capabilities.capabilities(
                command.control_command.target_runtime_id
            ).get(capability) == "available"
        except Exception:
            available = False
        if not available or self._runtime_control is None:
            return self._remember(command.operation_id, digest, _result(
                command.operation_id, "rejected", "sfu_egress_capability_unsupported",
            ))
        result = self._runtime_control.execute(command.control_command)
        outcome = "applied" if result.accepted and result.authenticated else "rejected"
        mapped = SfuEgressOperationResult(
            command.operation_id, outcome, result.reason_code, 1 if result.accepted else 0,
            retryable=not result.authenticated,
        )
        return self._remember(command.operation_id, digest, mapped)

    def observe(self, query: SfuEgressObservationQuery) -> SfuEgressObservationResult:
        if self._observations is None or not _valid_query(query):
            return SfuEgressObservationResult(False, "sfu_egress_observation_capability_unsupported", {})
        try:
            raw = self._observations.observe(query)
        except Exception:
            return SfuEgressObservationResult(False, "sfu_egress_observation_unavailable", {})
        if raw is None:
            return SfuEgressObservationResult(False, "sfu_egress_observation_capability_unsupported", {})
        if set(raw) != _OBSERVATION_KEYS or not _valid_observation(raw, query):
            return SfuEgressObservationResult(False, "sfu_egress_observation_invalid", {})
        return SfuEgressObservationResult(
            True, "sfu_egress_observation_non_authoritative", dict(raw),
        )

    def clear_receipts(self) -> None:
        self._receipts.clear()

    def _duplicate(self, operation_id: str, digest: str) -> SfuEgressOperationResult | None:
        existing = self._receipts.get(operation_id)
        if existing is None:
            return None
        if existing[0] != digest:
            return _result(operation_id, "rejected", "sfu_egress_idempotency_conflict")
        prior = existing[1]
        return SfuEgressOperationResult(
            operation_id, prior.outcome, "sfu_egress_duplicate_idempotent",
            prior.calls_completed, duplicate=True, retryable=False,
        )

    def _remember(self, operation_id: str, digest: str,
                  result: SfuEgressOperationResult) -> SfuEgressOperationResult:
        self._receipts[operation_id] = (digest, result)
        self._receipts.move_to_end(operation_id)
        while len(self._receipts) > self._receipt_limit:
            self._receipts.popitem(last=False)
        return result


def _validate_subscription(value: SfuEgressSubscriptionCommand, now_ms: int) -> str | None:
    if value.operation not in {"subscribe", "update", "unsubscribe"}:
        return "sfu_egress_operation_invalid"
    common = _validate_common(value, now_ms)
    if common is not None:
        return common
    if type(value.issued_at_ms) is not int or value.issued_at_ms > now_ms + 5000 \
            or value.expires_at_ms - value.issued_at_ms > 30_000:
        return "sfu_egress_command_stale"
    if not _digest_value(value.route_command_digest) \
            or not _digest_value(value.fairness_profile_digest):
        return "sfu_egress_digest_invalid"
    return None


def _validate_runtime(value: SfuEgressRuntimeActionCommand, now_ms: int) -> str | None:
    if value.action not in _RUNTIME_CAPABILITY \
            or value.control_command.command_type != f"sfu_egress_{value.action}" \
            or value.control_command.fencing_token != value.fencing_token \
            or value.control_command.tenant_id != value.tenant_id:
        return "sfu_egress_runtime_action_invalid"
    common = _validate_common(value, now_ms)
    if common is not None:
        return common
    issued_at_ms = int(value.control_command.issued_at * 1000)
    deadline_at_ms = int(value.control_command.deadline_at * 1000)
    if issued_at_ms > now_ms + 5000 or deadline_at_ms <= now_ms \
            or deadline_at_ms - issued_at_ms > 30_000:
        return "sfu_egress_command_stale"
    if not _digest_value(value.fairness_profile_digest) \
            or not _digest_value(value.control_command.config_digest):
        return "sfu_egress_digest_invalid"
    return None


def _validate_common(value: object, now_ms: int) -> str | None:
    for name in ("operation_id", "tenant_id", "room_id", "publication_id"):
        if not _handle(getattr(value, name, None)):
            return "sfu_egress_scope_invalid"
    for name in ("route_epoch", "topology_epoch", "fencing_token"):
        item = getattr(value, name, None)
        if type(item) is not int or item < 1:
            return "sfu_egress_epoch_invalid"
    expires = getattr(value, "expires_at_ms", None)
    if type(expires) is not int or expires <= now_ms or expires > now_ms + 30_000:
        return "sfu_egress_command_stale"
    return None


def _subscription_digest(value: SfuEgressSubscriptionCommand) -> str:
    return _canonical_digest({
        "operation": value.operation, "tenant_id": value.tenant_id,
        "room_id": value.room_id, "publication_id": value.publication_id,
        "route_epoch": value.route_epoch, "topology_epoch": value.topology_epoch,
        "fencing_token": value.fencing_token, "route_command_digest": value.route_command_digest,
        "fairness_profile_digest": value.fairness_profile_digest,
    })


def _runtime_digest(value: SfuEgressRuntimeActionCommand) -> str:
    return _canonical_digest({
        "action": value.action, "tenant_id": value.tenant_id, "room_id": value.room_id,
        "publication_id": value.publication_id, "route_epoch": value.route_epoch,
        "topology_epoch": value.topology_epoch, "fencing_token": value.fencing_token,
        "fairness_profile_digest": value.fairness_profile_digest,
        "runtime_command_id": value.control_command.command_id,
        "runtime_config_digest": value.control_command.config_digest,
    })


def _valid_query(value: SfuEgressObservationQuery) -> bool:
    return all(_handle(item) for item in (value.tenant_id, value.room_id, value.publication_id, value.node_id)) \
        and all(type(item) is int and item > 0 for item in (
            value.route_epoch, value.topology_epoch, value.fencing_token,
        ))


def _valid_observation(raw: Mapping[str, object], query: SfuEgressObservationQuery) -> bool:
    if any(raw.get(name) != getattr(query, name) for name in (
        "tenant_id", "room_id", "publication_id", "node_id", "route_epoch",
        "topology_epoch", "fencing_token",
    )):
        return False
    integers = ("window_started_at_ms", "window_ended_at_ms", "observable_drops", "receiver_count")
    if any(type(raw.get(name)) is not int or int(raw[name]) < 0 for name in integers):
        return False
    if not 100 <= int(raw["window_ended_at_ms"]) - int(raw["window_started_at_ms"]) <= 60_000:
        return False
    for name in ("actual_egress_bytes", "estimated_egress_bytes"):
        item = raw.get(name)
        if item is not None and (type(item) is not int or not 0 <= item <= 9_007_199_254_740_991):
            return False
    return True


def _handle(value: object) -> bool:
    return isinstance(value, str) and 1 <= len(value.encode()) <= 128 \
        and not any(ord(char) <= 0x20 or ord(char) == 0x7f for char in value)


def _digest_value(value: str) -> bool:
    candidate = value.removeprefix("sha256:")
    return len(candidate) == 64 and all(char in "0123456789abcdef" for char in candidate)


def _canonical_digest(value: Mapping[str, object]) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode()).hexdigest()


def _result(operation_id: str, outcome, reason: str, *, retryable: bool = False):
    return SfuEgressOperationResult(operation_id, outcome, reason, 0, retryable=retryable)


__all__ = [
    "LivekitBroadcastEgressAdapter", "LiveKitEgressControlClientPort",
    "SfuEgressCommandAuthorizationPort", "SfuEgressObservationQuery",
    "SfuEgressObservationResult", "SfuEgressObservationSourcePort",
    "SfuEgressOperationResult", "SfuEgressRuntimeActionCommand",
    "SfuEgressRuntimeCapabilityPort", "SfuEgressSubscriptionCommand",
]
