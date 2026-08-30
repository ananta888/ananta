"""Closed transport-neutral contracts for native collaboration workspaces."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any, ClassVar

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
ACTOR_KINDS = frozenset({"human", "agent", "worker", "resource", "service", "external_actor"})
AUTHORITY_KINDS = frozenset({"oidc", "hub_agent", "registered_worker", "resource_registry", "service", "bridge"})
ROOM_KINDS = frozenset({"project", "goal", "task", "branch", "incident", "pair_session", "freeform"})
VISIBILITY_CLASSES = frozenset({"workspace", "room", "restricted"})
RETENTION_CLASSES = frozenset({"ephemeral", "standard", "audit", "legal_hold"})
EVENT_TYPES = frozenset(
    {
        "message.posted",
        "message.replied",
        "decision.recorded",
        "review.recorded",
        "artifact.linked",
        "task.projected",
        "workflow.projected",
        "git.projected",
        "command.proposed",
        "command.decided",
        "membership.changed",
        "room.changed",
        "legacy.share_session.observed",
        "event.redacted",
    }
)


class CollaborationContractError(ValueError):
    pass


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def canonical_digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def require_id(value: object, field: str) -> str:
    text = str(value or "").strip()
    if not _ID.fullmatch(text):
        raise CollaborationContractError(f"collaboration_{field}_invalid")
    return text


def require_digest(value: object, field: str) -> str:
    text = str(value or "").strip().lower()
    if not _DIGEST.fullmatch(text):
        raise CollaborationContractError(f"collaboration_{field}_invalid")
    return text


def require_text(value: object, field: str, *, maximum: int = 256) -> str:
    text = str(value or "").strip()
    if not 1 <= len(text) <= maximum or any(ord(character) < 32 for character in text):
        raise CollaborationContractError(f"collaboration_{field}_invalid")
    return text


def _exact(value: Mapping[str, Any], fields: set[str], name: str) -> None:
    if set(value) != fields:
        raise CollaborationContractError(f"collaboration_{name}_fields_invalid")


def _ids(value: object, field: str, *, maximum: int = 64) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) > maximum:
        raise CollaborationContractError(f"collaboration_{field}_invalid")
    result = tuple(require_id(item, field) for item in value)
    if len(result) != len(set(result)):
        raise CollaborationContractError(f"collaboration_{field}_duplicate")
    return result


def _timestamp(value: object, field: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(float(value)):
        raise CollaborationContractError(f"collaboration_{field}_invalid")
    return float(value)


@dataclass(frozen=True, slots=True)
class WorkspaceActorBindingV1:
    SCHEMA: ClassVar[str] = "ananta.collaboration-actor-binding.v1"
    schema: str
    actor_binding_id: str
    actor_kind: str
    authority_kind: str
    authority_subject: str
    display_name: str
    capabilities: tuple[str, ...]

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> WorkspaceActorBindingV1:
        _exact(
            value,
            {
                "schema",
                "actor_binding_id",
                "actor_kind",
                "authority_kind",
                "authority_subject",
                "display_name",
                "capabilities",
            },
            "actor_binding",
        )
        actor_kind = str(value.get("actor_kind") or "").strip()
        authority_kind = str(value.get("authority_kind") or "").strip()
        display_name = str(value.get("display_name") or "").strip()
        if value.get("schema") != cls.SCHEMA or actor_kind not in ACTOR_KINDS or authority_kind not in AUTHORITY_KINDS:
            raise CollaborationContractError("collaboration_actor_binding_invalid")
        if not 1 <= len(display_name) <= 128:
            raise CollaborationContractError("collaboration_display_name_invalid")
        return cls(
            cls.SCHEMA,
            require_id(value.get("actor_binding_id"), "actor_binding_id"),
            actor_kind,
            authority_kind,
            require_text(value.get("authority_subject"), "authority_subject"),
            display_name,
            _ids(value.get("capabilities"), "actor_capabilities", maximum=32),
        )

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "capabilities": list(self.capabilities)}


@dataclass(frozen=True, slots=True)
class CollaborationRoomV1:
    SCHEMA: ClassVar[str] = "ananta.collaboration-room.v1"
    schema: str
    room_id: str
    room_kind: str
    title: str
    binding_kind: str | None
    binding_id: str | None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> CollaborationRoomV1:
        _exact(value, {"schema", "room_id", "room_kind", "title", "binding_kind", "binding_id"}, "room")
        kind = str(value.get("room_kind") or "").strip()
        title = str(value.get("title") or "").strip()
        binding_kind = str(value.get("binding_kind") or "").strip() or None
        binding_id = str(value.get("binding_id") or "").strip() or None
        if value.get("schema") != cls.SCHEMA or kind not in ROOM_KINDS or not 1 <= len(title) <= 200:
            raise CollaborationContractError("collaboration_room_invalid")
        if (binding_kind is None) != (binding_id is None):
            raise CollaborationContractError("collaboration_room_binding_invalid")
        if binding_kind is not None:
            binding_kind = require_id(binding_kind, "binding_kind")
            binding_id = require_id(binding_id, "binding_id")
        return cls(cls.SCHEMA, require_id(value.get("room_id"), "room_id"), kind, title, binding_kind, binding_id)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class WorkspaceEventV1:
    SCHEMA: ClassVar[str] = "ananta.workspace-event.v1"
    schema: str
    event_id: str
    workspace_id: str
    room_id: str | None
    thread_id: str | None
    event_type: str
    actor_binding_id: str
    idempotency_key: str
    correlation_id: str
    causation_id: str | None
    visibility: str
    retention: str
    occurred_at: float
    payload: Mapping[str, Any]
    payload_digest: str
    source_refs: tuple[str, ...]
    run_refs: tuple[str, ...]

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> WorkspaceEventV1:
        _exact(
            value,
            {
                "schema",
                "event_id",
                "workspace_id",
                "room_id",
                "thread_id",
                "event_type",
                "actor_binding_id",
                "idempotency_key",
                "correlation_id",
                "causation_id",
                "visibility",
                "retention",
                "occurred_at",
                "payload",
                "payload_digest",
                "source_refs",
                "run_refs",
            },
            "event",
        )
        event_type = str(value.get("event_type") or "").strip()
        visibility = str(value.get("visibility") or "").strip()
        retention = str(value.get("retention") or "").strip()
        payload = value.get("payload")
        if (
            value.get("schema") != cls.SCHEMA
            or event_type not in EVENT_TYPES
            or visibility not in VISIBILITY_CLASSES
            or retention not in RETENTION_CLASSES
            or not isinstance(payload, Mapping)
            or len(canonical_json(payload).encode()) > 65_536
        ):
            raise CollaborationContractError("collaboration_event_invalid")
        payload_digest = require_digest(value.get("payload_digest"), "payload_digest")
        if payload_digest != canonical_digest(payload):
            raise CollaborationContractError("collaboration_payload_digest_mismatch")
        source_refs = _ids(value.get("source_refs"), "source_refs", maximum=64)
        run_refs = _ids(value.get("run_refs"), "run_refs", maximum=64)
        if any(not item.startswith("SRC_") for item in source_refs) or any(
            not item.startswith("RUN_") for item in run_refs
        ):
            raise CollaborationContractError("collaboration_evidence_ref_invalid")
        grounded_types = {
            "decision.recorded",
            "review.recorded",
            "task.projected",
            "workflow.projected",
            "git.projected",
        }
        if event_type in grounded_types and (not source_refs or not run_refs):
            raise CollaborationContractError("collaboration_grounded_evidence_required")
        return cls(
            cls.SCHEMA,
            require_id(value.get("event_id"), "event_id"),
            require_id(value.get("workspace_id"), "workspace_id"),
            require_id(value.get("room_id"), "room_id") if value.get("room_id") is not None else None,
            require_id(value.get("thread_id"), "thread_id") if value.get("thread_id") is not None else None,
            event_type,
            require_id(value.get("actor_binding_id"), "actor_binding_id"),
            require_id(value.get("idempotency_key"), "idempotency_key"),
            require_id(value.get("correlation_id"), "correlation_id"),
            require_id(value.get("causation_id"), "causation_id") if value.get("causation_id") is not None else None,
            visibility,
            retention,
            _timestamp(value.get("occurred_at"), "occurred_at"),
            dict(payload),
            payload_digest,
            source_refs,
            run_refs,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "payload": dict(self.payload),
            "source_refs": list(self.source_refs),
            "run_refs": list(self.run_refs),
        }


__all__ = [
    "ACTOR_KINDS",
    "EVENT_TYPES",
    "ROOM_KINDS",
    "CollaborationContractError",
    "CollaborationRoomV1",
    "WorkspaceActorBindingV1",
    "WorkspaceEventV1",
    "canonical_digest",
    "canonical_json",
    "require_digest",
    "require_id",
    "require_text",
]
