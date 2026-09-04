"""Cross-language collaboration contract gate with stable reason codes."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any, Callable

from ananta_contracts.collaboration_resources import AgentIntentV1, SharedResourceOfferV1
from ananta_contracts.collaboration_workspace import (
    CollaborationRoomV1,
    CollaborationWorkspaceV1,
    WorkspaceActorBindingV1,
    WorkspaceEventV1,
    canonical_digest,
    canonical_json,
    require_digest,
    require_id,
)


class CollaborationContractGate:
    """Dispatches closed contracts and applies caller-independent scope guards."""

    def __init__(self) -> None:
        self._parsers: dict[str, Callable[[Mapping[str, Any]], dict[str, Any]]] = {
            "workspace": lambda value: CollaborationWorkspaceV1.from_mapping(value).to_dict(),
            "actor": lambda value: WorkspaceActorBindingV1.from_mapping(value).to_dict(),
            "room": lambda value: CollaborationRoomV1.from_mapping(value).to_dict(),
            "event": lambda value: WorkspaceEventV1.from_mapping(value).to_dict(),
            "resource": lambda value: SharedResourceOfferV1.from_mapping(value).to_dict(),
            "intent": lambda value: AgentIntentV1.from_mapping(value).to_dict(),
            "membership": self._membership,
            "live": self._live,
            "bridge_capability": self._bridge_capability,
        }

    def validate(
        self,
        contract: str,
        payload: Mapping[str, Any],
        *,
        expected_tenant_id: str | None = None,
        expected_workspace_id: str | None = None,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        try:
            parser = self._parsers[contract]
        except KeyError as exc:
            raise ValueError("collaboration_contract_kind_unknown") from exc
        result = parser(payload)
        if expected_tenant_id is not None and result.get("tenant_id") != expected_tenant_id:
            raise PermissionError("collaboration_tenant_scope_mismatch")
        if expected_workspace_id is not None and result.get("workspace_id") != expected_workspace_id:
            raise PermissionError("collaboration_workspace_scope_mismatch")
        if expected_revision is not None and result.get("revision") != expected_revision:
            raise PermissionError("collaboration_revision_stale")
        return result

    @staticmethod
    def _membership(value: Mapping[str, Any]) -> dict[str, Any]:
        fields = {"schema", "workspace_id", "actor_binding_id", "role", "status", "revision", "capabilities"}
        if set(value) != fields:
            raise ValueError("collaboration_membership_fields_invalid")
        revision = value.get("revision")
        capabilities = value.get("capabilities")
        if (
            value.get("schema") != "ananta.collaboration-membership.v1"
            or value.get("role") not in {"owner", "maintainer", "member", "guest", "observer", "editor", "viewer"}
            or value.get("status") not in {"active", "revoked"}
            or not isinstance(revision, int)
            or isinstance(revision, bool)
            or revision < 1
            or not isinstance(capabilities, list)
            or len(capabilities) > 32
            or len(capabilities) != len(set(capabilities))
        ):
            raise ValueError("collaboration_membership_invalid")
        return {
            **dict(value),
            "workspace_id": require_id(value["workspace_id"], "workspace_id"),
            "actor_binding_id": require_id(value["actor_binding_id"], "actor_binding_id"),
            "capabilities": [require_id(item, "membership_capability") for item in capabilities],
        }

    @staticmethod
    def _live(value: Mapping[str, Any]) -> dict[str, Any]:
        fields = {
            "schema",
            "envelope_id",
            "workspace_id",
            "room_id",
            "publisher_actor_binding_id",
            "traffic_class",
            "publisher_epoch",
            "created_at",
            "payload",
            "payload_digest",
            "durable_event_id",
        }
        if set(value) != fields:
            raise ValueError("collaboration_live_envelope_fields_invalid")
        payload = value.get("payload")
        epoch = value.get("publisher_epoch")
        created_at = value.get("created_at")
        if (
            value.get("schema") != "ananta.collaboration-live-envelope.v1"
            or value.get("traffic_class")
            not in {"revocation", "control", "durable_projection", "semantic", "presence", "bulk_reference"}
            or not isinstance(epoch, int)
            or isinstance(epoch, bool)
            or epoch < 1
            or not isinstance(created_at, (int, float))
            or isinstance(created_at, bool)
            or not math.isfinite(float(created_at))
            or not isinstance(payload, Mapping)
            or len(canonical_json(payload).encode()) > 65_536
            or "audience" in payload
            or "receivers" in payload
        ):
            raise ValueError("collaboration_live_envelope_invalid")
        digest = require_digest(value.get("payload_digest"), "payload_digest")
        if digest != canonical_digest(payload):
            raise ValueError("collaboration_live_payload_digest_mismatch")
        durable = value.get("durable_event_id")
        if value["traffic_class"] == "durable_projection" and durable is None:
            raise ValueError("collaboration_live_durable_identity_required")
        return {
            **dict(value),
            "envelope_id": require_id(value["envelope_id"], "envelope_id"),
            "workspace_id": require_id(value["workspace_id"], "workspace_id"),
            "room_id": require_id(value["room_id"], "room_id"),
            "publisher_actor_binding_id": require_id(value["publisher_actor_binding_id"], "publisher_actor_binding_id"),
            "created_at": float(created_at),
            "payload": dict(payload),
            "payload_digest": digest,
            "durable_event_id": require_id(durable, "durable_event_id") if durable is not None else None,
        }

    @staticmethod
    def _bridge_capability(value: Mapping[str, Any]) -> dict[str, Any]:
        fields = {
            "schema",
            "state",
            "mapping_versions",
            "supports_outbound",
            "supports_inbound_proposals",
            "supports_command_intents",
            "native_core_available",
        }
        if set(value) != fields:
            raise ValueError("collaboration_bridge_capability_fields_invalid")
        mappings = value.get("mapping_versions")
        flags = [
            value.get("supports_outbound"),
            value.get("supports_inbound_proposals"),
            value.get("supports_command_intents"),
            value.get("native_core_available"),
        ]
        if (
            value.get("schema") != "ananta.collaboration-bridge-capability.v1"
            or value.get("state") not in {"disabled", "disconnected", "connected"}
            or not isinstance(mappings, list)
            or len(mappings) > 16
            or len(mappings) != len(set(mappings))
            or not all(isinstance(item, bool) for item in flags)
            or value.get("native_core_available") is not True
        ):
            raise ValueError("collaboration_bridge_capability_invalid")
        return {**dict(value), "mapping_versions": [require_id(item, "mapping_version") for item in mappings]}


__all__ = ["CollaborationContractGate"]
