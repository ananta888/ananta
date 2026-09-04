"""Adapter-neutral bridge ports; external systems never become Hub authority."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from ananta_contracts.collaboration_workspace import require_id

PINNED_BUZZ_REVISION = "01bacb8df3d2f5718e0a468828e07ae874a38eae"
BUZZ_MAPPING_VERSION = "ananta-buzz-v1"


@dataclass(frozen=True, slots=True)
class BuzzBridgeConfig:
    tenant_id: str
    workspace_id: str
    adapter_id: str
    relay_host: str
    enabled: bool = False
    tls_required: bool = True
    mapping_version: str = BUZZ_MAPPING_VERSION
    pinned_revision: str = PINNED_BUZZ_REVISION
    community_id: str | None = None
    auth_ref: str | None = None
    signing_key_ref: str | None = None

    def __post_init__(self) -> None:
        require_id(self.tenant_id, "tenant_id")
        require_id(self.workspace_id, "workspace_id")
        require_id(self.adapter_id, "adapter_id")
        if self.community_id is not None:
            require_id(self.community_id, "community_id")
        if self.auth_ref is not None:
            require_id(self.auth_ref, "auth_ref")
        if self.signing_key_ref is not None:
            require_id(self.signing_key_ref, "signing_key_ref")
        if (
            not self.relay_host
            or "/" in self.relay_host
            or "@" in self.relay_host
            or self.mapping_version != BUZZ_MAPPING_VERSION
            or self.pinned_revision != PINNED_BUZZ_REVISION
        ):
            raise ValueError("buzz_bridge_config_invalid")


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


__all__ = [
    "BUZZ_MAPPING_VERSION",
    "PINNED_BUZZ_REVISION",
    "BuzzBridgeConfig",
    "CollaborationBridgePort",
    "DisabledCollaborationBridge",
]
