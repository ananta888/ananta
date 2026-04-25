from __future__ import annotations

from agent.services.bridge_adapter_registry import BridgeAdapterRegistry


def test_bridge_adapter_registry_resolves_known_adapter_as_ready() -> None:
    registry = BridgeAdapterRegistry(
        adapter_catalog={
            "example.bridge.v1": {
                "enabled": True,
                "communication_modes": ["http", "websocket"],
                "operations": ["health", "execute_action", "report_result"],
            }
        }
    )
    registry.load_from_descriptors(
        {"example": {"domain_id": "example", "bridge_adapter_type": "example.bridge.v1"}}
    )

    resolved = registry.resolve("example")

    assert resolved["status"] == "ready"
    assert resolved["adapter_type"] == "example.bridge.v1"
    assert resolved["allowed_communication_modes"] == ["http", "websocket"]


def test_bridge_adapter_registry_reports_unknown_adapter_as_degraded() -> None:
    registry = BridgeAdapterRegistry(adapter_catalog={"known.v1": {"enabled": True, "communication_modes": []}})
    registry.load_from_descriptors({"example": {"domain_id": "example", "bridge_adapter_type": "unknown.v1"}})

    resolved = registry.resolve("example")

    assert resolved["status"] == "degraded"
    assert resolved["reason"] == "unknown_adapter_type"


def test_bridge_adapter_registry_reports_disabled_adapter_as_degraded() -> None:
    registry = BridgeAdapterRegistry(
        adapter_catalog={"disabled.v1": {"enabled": False, "communication_modes": ["http"]}}
    )
    registry.load_from_descriptors({"example": {"domain_id": "example", "bridge_adapter_type": "disabled.v1"}})

    resolved = registry.resolve("example")

    assert resolved["status"] == "degraded"
    assert resolved["reason"] == "adapter_disabled"


def test_bridge_adapter_registry_reports_malformed_descriptor_as_degraded() -> None:
    registry = BridgeAdapterRegistry(adapter_catalog={"known.v1": {"enabled": True, "communication_modes": []}})
    registry.load_from_descriptors({"example": {"domain_id": "example"}})

    resolved = registry.resolve("example")

    assert resolved["status"] == "degraded"
    assert resolved["reason"] == "malformed_descriptor"

