"""Hub-owned durability and content admission policy for collaboration events."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from agent.services.collaboration_content_security import CollaborationSensitiveContentDetector
from ananta_contracts.collaboration_workspace import EVENT_TYPES, canonical_json


@dataclass(frozen=True, slots=True)
class CollaborationEventDecision:
    event_type: str
    traffic_class: str
    durable: bool
    admitted: bool
    reason_code: str


class CollaborationEventPolicy:
    EPHEMERAL_TYPES = frozenset({"cursor.moved", "typing.changed", "view.delta", "presence.heartbeat"})
    BULK_REFERENCE_TYPES = frozenset({"artifact.linked"})
    COMMAND_TYPES = frozenset({"command.proposed", "command.decided"})

    def __init__(self, detector: CollaborationSensitiveContentDetector | None = None) -> None:
        self._detector = detector or CollaborationSensitiveContentDetector()

    def classify(self, event_type: str) -> CollaborationEventDecision:
        normalized = str(event_type or "").strip()
        if normalized in self.EPHEMERAL_TYPES:
            return CollaborationEventDecision(normalized, "ephemeral", False, True, "ephemeral_only")
        if normalized not in EVENT_TYPES:
            return CollaborationEventDecision(normalized, "unknown", False, False, "event_type_unknown")
        if normalized in self.BULK_REFERENCE_TYPES:
            traffic_class = "bulk_reference"
        elif normalized in self.COMMAND_TYPES:
            traffic_class = "command_intent"
        else:
            traffic_class = "durable_collaboration"
        return CollaborationEventDecision(normalized, traffic_class, True, True, "durable_admitted")

    def require_durable(self, event_type: str, payload: Mapping[str, Any]) -> CollaborationEventDecision:
        decision = self.classify(event_type)
        if not decision.admitted:
            raise ValueError("collaboration_event_type_unknown")
        if not decision.durable:
            raise ValueError("collaboration_ephemeral_event_not_durable")
        violation = self._detector.sensitive_path(payload)
        if violation is not None:
            raise ValueError(f"collaboration_sensitive_content_rejected:{violation}")
        if len(canonical_json(payload).encode()) > 65_536:
            raise ValueError("collaboration_event_payload_too_large")
        if event_type == "artifact.linked":
            self._validate_artifact(payload)
        return decision

    @staticmethod
    def _validate_artifact(payload: Mapping[str, Any]) -> None:
        required = {"artifact_id", "digest", "size_bytes", "media_type", "scan_status", "export_allowed"}
        if not required.issubset(payload) or {"content", "bytes", "local_path"}.intersection(payload):
            raise ValueError("collaboration_artifact_reference_invalid")
        size = payload.get("size_bytes")
        digest = str(payload.get("digest") or "")
        if (
            not isinstance(size, int)
            or isinstance(size, bool)
            or not 0 <= size <= 10_000_000_000
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
            or payload.get("scan_status") != "clean"
            or not isinstance(payload.get("export_allowed"), bool)
        ):
            raise ValueError("collaboration_artifact_reference_invalid")


__all__ = ["CollaborationEventDecision", "CollaborationEventPolicy"]
