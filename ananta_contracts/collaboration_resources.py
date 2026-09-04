"""Closed contracts for Hub-controlled collaboration intents and resources."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any, ClassVar

from ananta_contracts.collaboration_workspace import canonical_digest, canonical_json, require_digest, require_id


def _exact(value: Mapping[str, Any], fields: set[str], name: str) -> None:
    if set(value) != fields:
        raise ValueError(f"collaboration_{name}_fields_invalid")


def _ids(value: object, field: str, maximum: int = 32) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) > maximum:
        raise ValueError(f"collaboration_{field}_invalid")
    result = tuple(require_id(item, field) for item in value)
    if not result or len(result) != len(set(result)):
        raise ValueError(f"collaboration_{field}_invalid")
    return result


def _time(value: object, field: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(float(value)):
        raise ValueError(f"collaboration_{field}_invalid")
    return float(value)


@dataclass(frozen=True, slots=True)
class SharedResourceOfferV1:
    SCHEMA: ClassVar[str] = "ananta.collaboration-resource-offer.v1"
    schema: str
    offer_id: str
    workspace_id: str
    owner_actor_binding_id: str
    resource_id: str
    capability_category: str
    capacity_class: str
    scopes: tuple[str, ...]
    expires_at: float
    sensitivity: str
    attestation_status: str
    metadata: Mapping[str, Any]

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> SharedResourceOfferV1:
        _exact(
            value,
            {
                "schema",
                "offer_id",
                "workspace_id",
                "owner_actor_binding_id",
                "resource_id",
                "capability_category",
                "capacity_class",
                "scopes",
                "expires_at",
                "sensitivity",
                "attestation_status",
                "metadata",
            },
            "resource_offer",
        )
        category = str(value.get("capability_category") or "")
        capacity = str(value.get("capacity_class") or "")
        sensitivity = str(value.get("sensitivity") or "")
        attestation = str(value.get("attestation_status") or "")
        metadata = value.get("metadata")
        if (
            value.get("schema") != cls.SCHEMA
            or category not in {"compute", "model", "repository", "terminal", "tool"}
            or capacity not in {"small", "medium", "large"}
            or sensitivity not in {"workspace", "restricted"}
            or attestation not in {"verified", "unverified", "test_only"}
            or not isinstance(metadata, Mapping)
        ):
            raise ValueError("collaboration_resource_offer_invalid")
        forbidden = {"endpoint", "private_endpoint", "raw_telemetry", "secret", "token", "local_path"}
        if any(str(key).casefold() in forbidden for key in metadata):
            raise ValueError("collaboration_resource_offer_sensitive_metadata")
        if len(canonical_json(metadata).encode()) > 4096:
            raise ValueError("collaboration_resource_offer_metadata_too_large")
        return cls(
            cls.SCHEMA,
            require_id(value.get("offer_id"), "offer_id"),
            require_id(value.get("workspace_id"), "workspace_id"),
            require_id(value.get("owner_actor_binding_id"), "owner_actor_binding_id"),
            require_id(value.get("resource_id"), "resource_id"),
            category,
            capacity,
            _ids(value.get("scopes"), "resource_scopes"),
            _time(value.get("expires_at"), "offer_expires_at"),
            sensitivity,
            attestation,
            dict(metadata),
        )

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "scopes": list(self.scopes), "metadata": dict(self.metadata)}


@dataclass(frozen=True, slots=True)
class AgentIntentV1:
    SCHEMA: ClassVar[str] = "ananta.collaboration-agent-intent.v1"
    schema: str
    intent_id: str
    workspace_id: str
    room_id: str
    actor_binding_id: str
    intent_type: str
    target_actor_binding_id: str | None
    task_id: str | None
    correlation_id: str
    causation_id: str | None
    hop_count: int
    payload: Mapping[str, Any]
    payload_digest: str
    origin_event_type: str | None = None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> AgentIntentV1:
        required = {
            "schema",
            "intent_id",
            "workspace_id",
            "room_id",
            "actor_binding_id",
            "intent_type",
            "target_actor_binding_id",
            "task_id",
            "correlation_id",
            "causation_id",
            "hop_count",
            "payload",
            "payload_digest",
        }
        if not required.issubset(value) or set(value) - (required | {"origin_event_type"}):
            raise ValueError("collaboration_agent_intent_fields_invalid")
        intent_type = str(value.get("intent_type") or "")
        payload = value.get("payload")
        hop_count = value.get("hop_count")
        if (
            value.get("schema") != cls.SCHEMA
            or intent_type not in {"mention", "answer", "propose_task", "request_context", "handoff_request"}
            or not isinstance(payload, Mapping)
            or not isinstance(hop_count, int)
            or isinstance(hop_count, bool)
            or not 0 <= hop_count <= 8
        ):
            raise ValueError("collaboration_agent_intent_invalid")
        forbidden = {"assignment_id", "budget", "provider", "team_id", "tools", "worker_id"}
        if cls._contains_forbidden(payload, forbidden):
            raise ValueError("collaboration_agent_intent_authority_escalation")
        origin_event_type = str(value.get("origin_event_type") or "").strip() or None
        if origin_event_type in {"workflow.projected", "task.projected"} and intent_type in {
            "propose_task",
            "handoff_request",
        }:
            raise ValueError("collaboration_agent_intent_workflow_retrigger_forbidden")
        digest = require_digest(value.get("payload_digest"), "payload_digest")
        if digest != canonical_digest(payload):
            raise ValueError("collaboration_agent_intent_digest_mismatch")
        return cls(
            cls.SCHEMA,
            require_id(value.get("intent_id"), "intent_id"),
            require_id(value.get("workspace_id"), "workspace_id"),
            require_id(value.get("room_id"), "room_id"),
            require_id(value.get("actor_binding_id"), "actor_binding_id"),
            intent_type,
            require_id(value.get("target_actor_binding_id"), "target_actor_binding_id")
            if value.get("target_actor_binding_id") is not None
            else None,
            require_id(value.get("task_id"), "task_id") if value.get("task_id") is not None else None,
            require_id(value.get("correlation_id"), "correlation_id"),
            require_id(value.get("causation_id"), "causation_id") if value.get("causation_id") is not None else None,
            hop_count,
            dict(payload),
            digest,
            origin_event_type,
        )

    def to_dict(self) -> dict[str, Any]:
        value = {**asdict(self), "payload": dict(self.payload)}
        if self.origin_event_type is None:
            value.pop("origin_event_type")
        return value

    @staticmethod
    def _contains_forbidden(value: Any, forbidden: set[str]) -> bool:
        if isinstance(value, Mapping):
            return any(
                str(key).casefold() in forbidden or AgentIntentV1._contains_forbidden(item, forbidden)
                for key, item in value.items()
            )
        if isinstance(value, (list, tuple)):
            return any(AgentIntentV1._contains_forbidden(item, forbidden) for item in value)
        return False


__all__ = ["AgentIntentV1", "SharedResourceOfferV1"]
