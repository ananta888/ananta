"""Optional default-off Buzz collaboration bridge; never an authority source."""

from __future__ import annotations

import json
import sqlite3
import time
from collections import defaultdict, deque
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Protocol

from agent.services.collaboration_bridge_ports import (
    BUZZ_MAPPING_VERSION,
    PINNED_BUZZ_REVISION,
    BuzzBridgeConfig,
)
from agent.services.collaboration_delivery_service import CollaborationDeliveryService
from agent.services.collaboration_event_policy import CollaborationEventPolicy
from agent.services.collaboration_operational_signals import CollaborationOperationalSignals
from ananta_contracts.collaboration_workspace import canonical_digest, canonical_json, require_id

MAPPING_VERSION = BUZZ_MAPPING_VERSION


class BuzzRelayClient(Protocol):
    def negotiate(self) -> Mapping[str, Any]: ...

    def publish(self, envelope: Mapping[str, Any], *, idempotency_key: str) -> Mapping[str, Any]: ...


class BuzzKeyProvider(Protocol):
    def sign(self, *, tenant_id: str, workspace_id: str, actor_binding_id: str, payload: bytes) -> str: ...


class BuzzSignatureVerifier(Protocol):
    def verify(self, *, external_actor_id: str, payload: bytes, signature: str) -> bool: ...


class BuzzBridgeDeliveryStore:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self._path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS buzz_bridge_deliveries(
                    tenant_id TEXT, workspace_id TEXT, adapter_id TEXT,
                    internal_event_id TEXT, external_event_id TEXT, mapping_version TEXT,
                    payload_digest TEXT, attempt INTEGER, status TEXT, error_class TEXT,
                    payload_json TEXT,
                    PRIMARY KEY(tenant_id,workspace_id,adapter_id,internal_event_id)
                )
                """
            )

    def get(self, config: BuzzBridgeConfig, internal_event_id: str) -> dict[str, Any] | None:
        with sqlite3.connect(self._path) as connection:
            row = connection.execute(
                "SELECT payload_json FROM buzz_bridge_deliveries WHERE tenant_id=? AND workspace_id=? "
                "AND adapter_id=? AND internal_event_id=?",
                (config.tenant_id, config.workspace_id, config.adapter_id, internal_event_id),
            ).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, config: BuzzBridgeConfig, record: Mapping[str, Any]) -> dict[str, Any]:
        with sqlite3.connect(self._path) as connection:
            connection.execute(
                "INSERT INTO buzz_bridge_deliveries(tenant_id,workspace_id,adapter_id,internal_event_id,"
                "external_event_id,mapping_version,payload_digest,attempt,status,error_class,payload_json) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(tenant_id,workspace_id,adapter_id,internal_event_id) "
                "DO UPDATE SET external_event_id=excluded.external_event_id,attempt=excluded.attempt,"
                "status=excluded.status,error_class=excluded.error_class,payload_json=excluded.payload_json",
                (
                    config.tenant_id,
                    config.workspace_id,
                    config.adapter_id,
                    record["internal_event_id"],
                    record.get("external_event_id"),
                    MAPPING_VERSION,
                    record["payload_digest"],
                    record["attempt"],
                    record["status"],
                    record.get("error_class"),
                    canonical_json(record),
                ),
            )
        return dict(record)


class BuzzCollaborationBridge:
    OUTBOUND_MAPPING = {
        "message.posted": "buzz.message.v1",
        "message.replied": "buzz.reply.v1",
        "decision.recorded": "buzz.decision.v1",
        "review.recorded": "buzz.review.v1",
        "artifact.linked": "buzz.artifact-reference.v1",
    }
    INBOUND_PASSIVE = {value: key for key, value in OUTBOUND_MAPPING.items()}
    INBOUND_COMMANDS = frozenset({"buzz.task-request.v1", "buzz.command-intent.v1"})

    def __init__(
        self,
        config: BuzzBridgeConfig,
        *,
        relay: BuzzRelayClient,
        keys: BuzzKeyProvider,
        signatures: BuzzSignatureVerifier,
        deliveries: BuzzBridgeDeliveryStore,
        inbox: CollaborationDeliveryService,
        actor_mapping: Callable[[str], str | None],
        room_mapping: Callable[[str], str | None],
        membership_active: Callable[[str, str], bool],
        clock: Callable[[], float] = time.time,
        operational_signals: CollaborationOperationalSignals | None = None,
    ) -> None:
        self._config = config
        self._relay = relay
        self._keys = keys
        self._signatures = signatures
        self._deliveries = deliveries
        self._inbox = inbox
        self._actor_mapping = actor_mapping
        self._room_mapping = room_mapping
        self._membership_active = membership_active
        self._clock = clock
        self._operational_signals = operational_signals
        self._connected = False
        self._rates: dict[str, deque[float]] = defaultdict(deque)
        self._content_policy = CollaborationEventPolicy()

    @property
    def capabilities(self) -> Mapping[str, Any]:
        return {
            "schema": "ananta.collaboration-bridge-capability.v1",
            "state": "connected" if self._connected else "disabled" if not self._config.enabled else "disconnected",
            "mapping_versions": [MAPPING_VERSION],
            "supports_outbound": self._connected,
            "supports_inbound_proposals": self._connected,
            "supports_command_intents": self._connected,
            "native_core_available": True,
        }

    def connect(self) -> Mapping[str, Any]:
        if not self._config.enabled:
            return self._connect_result(False, "buzz_bridge_disabled", "blocked")
        try:
            remote = dict(self._relay.negotiate())
        except Exception as exc:
            del exc
            return self._connect_result(False, "buzz_relay_unavailable", "error")
        if remote.get("mapping_version") != MAPPING_VERSION or remote.get("revision") != PINNED_BUZZ_REVISION:
            return self._connect_result(False, "buzz_capability_mismatch", "blocked")
        self._connected = True
        return self._connect_result(True, "buzz_bridge_connected", "success")

    def disconnect(self) -> Mapping[str, Any]:
        self._connected = False
        return {"connected": False, "reason_code": "buzz_bridge_disconnected", "native_core_available": True}

    def deliver(self, event: Mapping[str, Any]) -> Mapping[str, Any]:
        if not self._connected:
            raise RuntimeError("buzz_bridge_not_connected")
        event_id = require_id(event.get("event_id"), "event_id")
        existing = self._deliveries.get(self._config, event_id)
        payload_digest = str(event.get("payload_digest") or "")
        if existing and existing["payload_digest"] != payload_digest:
            raise ValueError("buzz_delivery_event_mutated")
        if existing and existing["status"] == "delivered":
            return {**existing, "replayed": True}
        event_type = str(event.get("event_type") or "")
        external_kind = self.OUTBOUND_MAPPING.get(event_type)
        if external_kind is None or event.get("visibility") == "restricted":
            raise ValueError("buzz_event_export_forbidden")
        room_id = event.get("room_id")
        actor_id = require_id(event.get("actor_binding_id"), "actor_binding_id")
        if room_id is not None and not self._membership_active(actor_id, str(room_id)):
            raise PermissionError("buzz_export_membership_stale")
        self._content_policy.require_durable(event_type, event.get("payload") or {})
        if event_type == "artifact.linked" and not (event.get("payload") or {}).get("export_allowed"):
            raise ValueError("buzz_artifact_export_forbidden")
        external = {
            "mapping_version": MAPPING_VERSION,
            "kind": external_kind,
            "internal_event_id": event_id,
            "room_id": event.get("room_id"),
            "payload": dict(event.get("payload") or {}),
            "payload_digest": payload_digest,
            "origin_adapter_id": self._config.adapter_id,
            "hop_count": 1,
        }
        signature = self._keys.sign(
            tenant_id=self._config.tenant_id,
            workspace_id=self._config.workspace_id,
            actor_binding_id=actor_id,
            payload=canonical_json(external).encode(),
        )
        attempt = int((existing or {}).get("attempt") or 0) + 1
        try:
            response = dict(self._relay.publish({**external, "signature": signature}, idempotency_key=event_id))
            external_id = require_id(response.get("external_event_id"), "external_event_id")
            record = {
                "internal_event_id": event_id,
                "external_event_id": external_id,
                "mapping_version": MAPPING_VERSION,
                "payload_digest": payload_digest,
                "attempt": attempt,
                "status": "delivered",
                "error_class": None,
            }
        except Exception as exc:
            del exc
            record = {
                "internal_event_id": event_id,
                "external_event_id": None,
                "mapping_version": MAPPING_VERSION,
                "payload_digest": payload_digest,
                "attempt": attempt,
                "status": "retry",
                "error_class": "relay_delivery_failed",
            }
        return self._deliveries.put(self._config, record)

    def propose(self, envelope: Mapping[str, Any]) -> Mapping[str, Any]:
        if not self._connected:
            raise RuntimeError("buzz_bridge_not_connected")
        required = {
            "external_event_id",
            "mapping_version",
            "kind",
            "external_actor_id",
            "external_room_id",
            "payload",
            "payload_digest",
            "signature",
            "origin_adapter_id",
            "hop_count",
        }
        if set(envelope) != required or len(canonical_json(envelope).encode()) > 65_536:
            raise ValueError("buzz_inbound_envelope_invalid")
        if envelope["mapping_version"] != MAPPING_VERSION or envelope["hop_count"] not in {0, 1}:
            raise ValueError("buzz_inbound_mapping_or_hop_invalid")
        if envelope["origin_adapter_id"] == self._config.adapter_id:
            raise ValueError("buzz_bridge_echo_rejected")
        kind = str(envelope["kind"])
        if kind not in self.INBOUND_PASSIVE and kind not in self.INBOUND_COMMANDS:
            raise ValueError("buzz_inbound_kind_unsupported")
        payload = envelope["payload"]
        if not isinstance(payload, Mapping) or canonical_digest(payload) != envelope["payload_digest"]:
            raise ValueError("buzz_inbound_payload_digest_invalid")
        actor = self._actor_mapping(str(envelope["external_actor_id"]))
        room = self._room_mapping(str(envelope["external_room_id"]))
        if actor is None or room is None or not self._membership_active(actor, room):
            raise PermissionError("buzz_inbound_scope_unavailable")
        self._check_rate(actor)
        signed = {key: value for key, value in envelope.items() if key != "signature"}
        if not self._signatures.verify(
            external_actor_id=str(envelope["external_actor_id"]),
            payload=canonical_json(signed).encode(),
            signature=str(envelope["signature"]),
        ):
            raise PermissionError("buzz_signature_invalid")
        admitted = self._inbox.admit_external(
            self._config.tenant_id,
            origin=str(envelope["origin_adapter_id"]),
            adapter_id=self._config.adapter_id,
            external_event_id=str(envelope["external_event_id"]),
            mapping_version=MAPPING_VERSION,
            payload_digest=str(envelope["payload_digest"]),
        )
        if kind in self.INBOUND_COMMANDS:
            return {
                **admitted,
                "proposal_type": "hub_command_intent_required",
                "authority_granted": False,
                "actor_binding_id": actor,
                "room_id": room,
            }
        event_type = self.INBOUND_PASSIVE[kind]
        self._content_policy.require_durable(event_type, payload)
        return {
            **admitted,
            "proposal_type": "passive_workspace_event",
            "event_type": event_type,
            "actor_binding_id": actor,
            "room_id": room,
            "authority_granted": False,
        }

    def _check_rate(self, actor: str) -> None:
        now = self._clock()
        window = self._rates[actor]
        while window and window[0] <= now - 60:
            window.popleft()
        if len(window) >= 60:
            raise PermissionError("buzz_inbound_rate_limited")
        window.append(now)

    def _connect_result(self, connected: bool, reason_code: str, outcome: str) -> dict[str, Any]:
        if self._operational_signals is not None:
            self._operational_signals.record("bridge_reconnect", outcome=outcome)
        return {"connected": connected, "reason_code": reason_code}


def buzz_bridge_conformance(*, runtime_evidence_verified: bool) -> dict[str, Any]:
    return {
        "schema": "ananta.buzz-bridge-conformance.v1",
        "pinned_revision": PINNED_BUZZ_REVISION,
        "mapping_version": MAPPING_VERSION,
        "local_conformance": "passed",
        "runtime_evidence": "verified" if runtime_evidence_verified else "unverified",
        "release_allowed": runtime_evidence_verified,
        "native_core_available": True,
        "native_core_gate_affected": False,
        "human_intervention_required": False,
    }


__all__ = [
    "BuzzBridgeConfig",
    "BuzzBridgeDeliveryStore",
    "BuzzCollaborationBridge",
    "MAPPING_VERSION",
    "PINNED_BUZZ_REVISION",
    "buzz_bridge_conformance",
]
