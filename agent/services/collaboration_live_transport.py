"""Policy-driven ephemeral collaboration transport primitives."""

from __future__ import annotations

import time
from collections import defaultdict, deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any

from ananta_contracts.collaboration_workspace import canonical_json, require_id


@dataclass(frozen=True, slots=True)
class LiveTopologyDecision:
    topology: str
    reason_code: str
    contract_revision: int
    expires_at: float


class CollaborationLiveTopologySelector:
    def __init__(
        self,
        *,
        sfu_release_state: str,
        relay_ready: bool,
        contract_revision: int = 1,
        decision_ttl_seconds: float = 30.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if sfu_release_state not in {"go", "no_go", "observe_only"}:
            raise ValueError("collaboration_sfu_release_state_invalid")
        self._sfu_release_state = sfu_release_state
        self._relay_ready = relay_ready
        self._revision = contract_revision
        self._ttl = decision_ttl_seconds
        self._clock = clock

    def select(
        self,
        *,
        participant_count: int,
        requested: str = "auto",
        e2ee_ready: bool,
        safe_fallback_allowed: bool = True,
    ) -> LiveTopologyDecision:
        if participant_count < 1 or requested not in {"auto", "p2p", "relay", "sfu"}:
            raise ValueError("collaboration_live_topology_request_invalid")
        expires_at = self._clock() + self._ttl
        if requested == "sfu" and self._sfu_release_state != "go":
            if safe_fallback_allowed and self._relay_ready:
                return LiveTopologyDecision("relay", "sfu_release_gate_fallback", self._revision, expires_at)
            return LiveTopologyDecision("unavailable", "sfu_release_gate_closed", self._revision, expires_at)
        if requested == "sfu" or (requested == "auto" and participant_count > 2 and self._sfu_release_state == "go"):
            return LiveTopologyDecision("sfu", "sfu_policy_selected", self._revision, expires_at)
        if requested == "relay" or participant_count > 2:
            topology = "relay" if self._relay_ready else "unavailable"
            reason = "relay_policy_selected" if self._relay_ready else "relay_unavailable"
            return LiveTopologyDecision(topology, reason, self._revision, expires_at)
        if requested in {"auto", "p2p"} and e2ee_ready:
            return LiveTopologyDecision("p2p", "p2p_e2ee_selected", self._revision, expires_at)
        if safe_fallback_allowed and self._relay_ready:
            return LiveTopologyDecision("relay", "p2p_e2ee_fallback", self._revision, expires_at)
        return LiveTopologyDecision("unavailable", "e2ee_required", self._revision, expires_at)


@dataclass(frozen=True, slots=True)
class TrafficClassPolicy:
    maximum_payload_bytes: int
    maximum_queue_items: int
    ttl_seconds: float
    priority: int


TRAFFIC_POLICIES = {
    "revocation": TrafficClassPolicy(2048, 32, 30.0, 100),
    "control": TrafficClassPolicy(4096, 64, 30.0, 90),
    "durable_projection": TrafficClassPolicy(65_536, 256, 300.0, 60),
    "semantic": TrafficClassPolicy(16_384, 64, 5.0, 30),
    "presence": TrafficClassPolicy(2048, 16, 3.0, 20),
    "bulk_reference": TrafficClassPolicy(8192, 16, 60.0, 10),
}


@dataclass(frozen=True, slots=True)
class LiveReceiverState:
    actor_binding_id: str
    active: bool
    epoch: int
    subscriptions: frozenset[str]


@dataclass(frozen=True, slots=True)
class LiveEnvelope:
    envelope_id: str
    workspace_id: str
    room_id: str
    publisher_actor_binding_id: str
    traffic_class: str
    publisher_epoch: int
    created_at: float
    payload: Mapping[str, Any]
    durable_event_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "payload": dict(self.payload)}


class CollaborationLiveRouter:
    """Per-receiver queues; caller-supplied audience fields never select recipients."""

    def __init__(
        self,
        receiver_state: Callable[[str, str, str], LiveReceiverState | None],
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._receiver_state = receiver_state
        self._clock = clock
        self._queues: dict[str, deque[LiveEnvelope]] = defaultdict(deque)
        self._seen: dict[str, set[str]] = defaultdict(set)
        self._acks: dict[str, set[str]] = defaultdict(set)

    def publish(self, envelope: LiveEnvelope, *, server_selected_receivers: Sequence[str]) -> dict[str, Any]:
        self._validate_envelope(envelope)
        publisher = self._receiver_state(envelope.workspace_id, envelope.room_id, envelope.publisher_actor_binding_id)
        if publisher is None or not publisher.active or publisher.epoch != envelope.publisher_epoch:
            raise PermissionError("collaboration_live_publisher_binding_invalid")
        recipients: list[str] = []
        dropped: dict[str, str] = {}
        for receiver_id in sorted(set(server_selected_receivers)):
            receiver = require_id(receiver_id, "receiver_actor_binding_id")
            state = self._receiver_state(envelope.workspace_id, envelope.room_id, receiver)
            if state is None or not state.active:
                dropped[receiver] = "receiver_membership_inactive"
                continue
            if state.epoch != envelope.publisher_epoch:
                dropped[receiver] = "receiver_epoch_mismatch"
                continue
            if envelope.traffic_class not in state.subscriptions:
                dropped[receiver] = "receiver_not_subscribed"
                continue
            if envelope.envelope_id in self._seen[receiver]:
                dropped[receiver] = "duplicate_envelope"
                continue
            if not self._enqueue(receiver, envelope):
                dropped[receiver] = "receiver_backpressure"
                continue
            self._seen[receiver].add(envelope.envelope_id)
            recipients.append(receiver)
        return {"recipients": recipients, "dropped": dropped, "publisher_audience_ignored": True}

    def receive(self, actor_binding_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
        actor = require_id(actor_binding_id, "actor_binding_id")
        if not 1 <= limit <= 100:
            raise ValueError("collaboration_live_receive_limit_invalid")
        now = self._clock()
        queue = self._queues[actor]
        self._queues[actor] = queue = deque(
            item for item in queue if item.created_at + TRAFFIC_POLICIES[item.traffic_class].ttl_seconds > now
        )
        values = list(queue)[:limit]
        return [value.to_dict() for value in values]

    def acknowledge(self, actor_binding_id: str, envelope_id: str) -> None:
        actor = require_id(actor_binding_id, "actor_binding_id")
        envelope = require_id(envelope_id, "envelope_id")
        self._acks[actor].add(envelope)
        self._queues[actor] = deque(item for item in self._queues[actor] if item.envelope_id != envelope)

    def revoke(self, actor_binding_id: str) -> int:
        actor = require_id(actor_binding_id, "actor_binding_id")
        removed = len(self._queues[actor])
        self._queues.pop(actor, None)
        self._seen.pop(actor, None)
        self._acks.pop(actor, None)
        return removed

    def _enqueue(self, receiver: str, envelope: LiveEnvelope) -> bool:
        queue = self._queues[receiver]
        policy = TRAFFIC_POLICIES[envelope.traffic_class]
        same_class = [item for item in queue if item.traffic_class == envelope.traffic_class]
        if len(same_class) < policy.maximum_queue_items:
            queue.append(envelope)
            return True
        lower = [item for item in queue if TRAFFIC_POLICIES[item.traffic_class].priority < policy.priority]
        if not lower:
            return False
        victim = min(lower, key=lambda item: TRAFFIC_POLICIES[item.traffic_class].priority)
        queue.remove(victim)
        queue.append(envelope)
        return True

    def _validate_envelope(self, envelope: LiveEnvelope) -> None:
        require_id(envelope.envelope_id, "envelope_id")
        require_id(envelope.workspace_id, "workspace_id")
        require_id(envelope.room_id, "room_id")
        require_id(envelope.publisher_actor_binding_id, "publisher_actor_binding_id")
        if envelope.traffic_class not in TRAFFIC_POLICIES:
            raise ValueError("collaboration_live_traffic_class_invalid")
        if envelope.publisher_epoch < 1:
            raise ValueError("collaboration_live_epoch_invalid")
        if "audience" in envelope.payload or "receivers" in envelope.payload:
            raise ValueError("collaboration_live_audience_escalation")
        if (
            len(canonical_json(envelope.payload).encode())
            > TRAFFIC_POLICIES[envelope.traffic_class].maximum_payload_bytes
        ):
            raise ValueError("collaboration_live_payload_too_large")
        if envelope.traffic_class == "durable_projection" and envelope.durable_event_id is None:
            raise ValueError("collaboration_live_durable_identity_required")


class CollaborationTransportCircuitBreaker:
    def __init__(self, *, failure_threshold: int = 3, backoff_seconds: float = 10.0) -> None:
        self._threshold = failure_threshold
        self._backoff = backoff_seconds
        self._failures = 0
        self._opened_at: float | None = None

    def record_failure(self, now: float) -> None:
        self._failures += 1
        if self._failures >= self._threshold:
            self._opened_at = now

    def allow(self, now: float) -> tuple[bool, str]:
        if self._opened_at is None:
            return True, "transport_closed"
        if now < self._opened_at + self._backoff:
            return False, "transport_backoff"
        return True, "transport_bounded_probe"

    def record_success(self) -> None:
        self._failures = 0
        self._opened_at = None


class BoundedCollaborationOfflineOutbox:
    ALLOWED_EVENT_TYPES = frozenset({"message.posted", "message.replied", "command.proposed"})

    def __init__(
        self,
        *,
        maximum_items: int = 100,
        maximum_bytes: int = 1_000_000,
        ttl_seconds: float = 3600.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if maximum_items < 1 or maximum_bytes < 1 or ttl_seconds <= 0:
            raise ValueError("collaboration_offline_outbox_limits_invalid")
        self._maximum_items = maximum_items
        self._maximum_bytes = maximum_bytes
        self._ttl = ttl_seconds
        self._clock = clock
        self._items: dict[str, tuple[float, int, dict[str, Any]]] = {}

    def enqueue(self, event: Mapping[str, Any]) -> dict[str, Any]:
        event_id = require_id(event.get("event_id"), "event_id")
        event_type = str(event.get("event_type") or "")
        if event_type not in self.ALLOWED_EVENT_TYPES:
            raise ValueError("collaboration_offline_event_type_rejected")
        serialized = canonical_json(event).encode()
        self._discard_expired()
        current = self._items.get(event_id)
        if current:
            if current[2] != dict(event):
                raise ValueError("collaboration_offline_event_conflict")
            return {"event_id": event_id, "replayed": True}
        if (
            len(self._items) >= self._maximum_items
            or sum(item[1] for item in self._items.values()) + len(serialized) > self._maximum_bytes
        ):
            raise OverflowError("collaboration_offline_outbox_full")
        self._items[event_id] = (self._clock(), len(serialized), dict(event))
        return {"event_id": event_id, "replayed": False}

    def flush(self, admit: Callable[[Mapping[str, Any]], Mapping[str, Any]]) -> dict[str, Any]:
        self._discard_expired()
        delivered: list[str] = []
        conflicts: list[dict[str, str]] = []
        for event_id, (_created_at, _size, event) in list(self._items.items()):
            result = dict(admit(event))
            if result.get("accepted") or result.get("replayed"):
                delivered.append(event_id)
                self._items.pop(event_id, None)
            else:
                conflicts.append(
                    {"event_id": event_id, "reason_code": str(result.get("reason_code") or "admission_rejected")}
                )
        return {"delivered": delivered, "conflicts": conflicts, "remaining": len(self._items)}

    def _discard_expired(self) -> None:
        now = self._clock()
        self._items = {event_id: item for event_id, item in self._items.items() if item[0] + self._ttl > now}


def collaboration_transport_health(
    components: Mapping[str, str], *, projection_lag: int, maximum_projection_lag: int
) -> dict[str, Any]:
    expected = {"signaling", "datachannel", "relay", "sfu", "turn", "e2ee"}
    allowed_states = {"ready", "degraded", "disabled", "failed"}
    if set(components) != expected or any(value not in allowed_states for value in components.values()):
        raise ValueError("collaboration_transport_health_invalid")
    if projection_lag < 0 or maximum_projection_lag < 0:
        raise ValueError("collaboration_projection_lag_invalid")
    required_failed = any(components[name] == "failed" for name in ("signaling", "relay", "e2ee"))
    degraded = required_failed or projection_lag > maximum_projection_lag or "degraded" in components.values()
    return {
        "state": "degraded" if degraded else "ready",
        "components": dict(components),
        "projection_lag": projection_lag,
        "reason_code": "transport_component_unhealthy" if degraded else "transport_healthy",
    }


__all__ = [
    "CollaborationLiveRouter",
    "CollaborationLiveTopologySelector",
    "CollaborationTransportCircuitBreaker",
    "BoundedCollaborationOfflineOutbox",
    "LiveEnvelope",
    "LiveReceiverState",
    "LiveTopologyDecision",
    "TRAFFIC_POLICIES",
    "collaboration_transport_health",
]
