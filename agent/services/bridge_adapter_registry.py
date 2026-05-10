from __future__ import annotations

from typing import Any

from agent.services.hermes_worker_profile import get_default_hermes_profile

DEFAULT_ADAPTER_CATALOG: dict[str, dict[str, Any]] = {
    "none": {
        "enabled": False,
        "communication_modes": [],
        "operations": [],
    },
    "local_client_bridge_v1": {
        "enabled": True,
        "communication_modes": ["http", "websocket"],
        "operations": ["health", "capabilities", "capture_context", "execute_action", "report_result", "cancel"],
    },
}

DEFAULT_FEATURE_FLAGS: dict[str, bool] = {
    "enable_hermes_worker_adapter": False,
}


class BridgeAdapterRegistry:
    """Resolve bridge adapter metadata from descriptors using an allow-list catalog."""

    def __init__(
        self,
        *,
        adapter_catalog: dict[str, dict[str, Any]] | None = None,
        feature_flags: dict[str, bool] | None = None,
    ) -> None:
        self.adapter_catalog = {key: dict(value) for key, value in (adapter_catalog or DEFAULT_ADAPTER_CATALOG).items()}
        self.feature_flags = {key: bool(value) for key, value in (feature_flags or DEFAULT_FEATURE_FLAGS).items()}
        self._entries_by_domain: dict[str, dict[str, Any]] = {}

    def load_from_descriptors(self, descriptors: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
        entries: dict[str, dict[str, Any]] = {}
        for descriptor_key, descriptor in descriptors.items():
            domain_id = str(descriptor.get("domain_id") or descriptor_key).strip()
            adapter_type = str(descriptor.get("bridge_adapter_type") or "").strip()
            if not domain_id or not adapter_type:
                target_key = domain_id or str(descriptor_key).strip() or "<unknown>"
                entries[target_key] = {
                    "status": "degraded",
                    "reason": "malformed_descriptor",
                    "adapter_type": adapter_type or None,
                    "allowed_communication_modes": [],
                }
                continue
            adapter_meta = self.adapter_catalog.get(adapter_type)
            if adapter_meta is None:
                entries[domain_id] = {
                    "status": "degraded",
                    "reason": "unknown_adapter_type",
                    "adapter_type": adapter_type,
                    "allowed_communication_modes": [],
                }
                continue
            if not bool(adapter_meta.get("enabled")):
                entries[domain_id] = {
                    "status": "degraded",
                    "reason": "adapter_disabled",
                    "adapter_type": adapter_type,
                    "allowed_communication_modes": list(adapter_meta.get("communication_modes") or []),
                }
                continue
            entries[domain_id] = {
                "status": "ready",
                "reason": "adapter_registered",
                "adapter_type": adapter_type,
                "allowed_communication_modes": list(adapter_meta.get("communication_modes") or []),
                "operations": list(adapter_meta.get("operations") or []),
            }
        self._entries_by_domain = entries
        return {domain_id: dict(entry) for domain_id, entry in entries.items()}

    def hermes_entry(self, *, enabled: bool, health_status: str = "disabled") -> dict[str, Any]:
        profile = get_default_hermes_profile()
        feature_enabled = bool(self.feature_flags.get("enable_hermes_worker_adapter", False))
        if not feature_enabled:
            return {
                "id": "hermes",
                "status": "degraded",
                "reason": "disabled_by_feature_flag",
                "kind": "external_agent_worker",
                "health_status": "disabled",
                "allowed_capability_classes": list(profile.allowed_capabilities),
                "denied_capability_classes": list(profile.denied_capabilities),
            }
        if not enabled:
            return {
                "id": "hermes",
                "status": "degraded",
                "reason": "adapter_disabled",
                "kind": "external_agent_worker",
                "health_status": "disabled",
                "allowed_capability_classes": list(profile.allowed_capabilities),
                "denied_capability_classes": list(profile.denied_capabilities),
            }
        return {
            "id": "hermes",
            "status": "ready",
            "reason": "adapter_registered",
            "kind": "external_agent_worker",
            "health_status": health_status,
            "allowed_capability_classes": list(profile.allowed_capabilities),
            "denied_capability_classes": list(profile.denied_capabilities),
            "phase": profile.phase,
        }

    def resolve(self, domain_id: str) -> dict[str, Any]:
        normalized_domain = str(domain_id).strip()
        entry = self._entries_by_domain.get(normalized_domain)
        if entry is None:
            return {
                "status": "degraded",
                "reason": "unknown_domain",
                "adapter_type": None,
                "allowed_communication_modes": [],
            }
        return dict(entry)

    def list_ready_domains(self) -> list[str]:
        return sorted(
            domain_id
            for domain_id, entry in self._entries_by_domain.items()
            if str(entry.get("status") or "").strip().lower() == "ready"
        )
