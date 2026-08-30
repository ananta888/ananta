"""Adapter-neutral bridge ports; external systems never become Hub authority."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol


class CollaborationBridgePort(Protocol):
    @property
    def capabilities(self) -> Mapping[str, Any]: ...

    def deliver(self, event: Mapping[str, Any]) -> Mapping[str, Any]: ...

    def propose(self, envelope: Mapping[str, Any]) -> Mapping[str, Any]: ...


class DisabledCollaborationBridge:
    @property
    def capabilities(self) -> Mapping[str, Any]:
        return {
            "schema": "ananta.collaboration-bridge-capability.v1",
            "state": "disabled",
            "mapping_versions": [],
            "supports_outbound": False,
            "supports_inbound_proposals": False,
            "supports_command_intents": False,
            "native_core_available": True,
        }

    def deliver(self, event: Mapping[str, Any]) -> Mapping[str, Any]:
        raise RuntimeError("collaboration_bridge_disabled")

    def propose(self, envelope: Mapping[str, Any]) -> Mapping[str, Any]:
        raise RuntimeError("collaboration_bridge_disabled")


__all__ = ["CollaborationBridgePort", "DisabledCollaborationBridge"]
