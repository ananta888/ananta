from __future__ import annotations

from typing import Any

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


class BridgeAdapterRegistry:
    """Resolve bridge adapter metadata from descriptors using an allow-list catalog."""

    def __init__(self, *, adapter_catalog: dict[str, dict[str, Any]] | None = None) -> None:
        self.adapter_catalog = {key: dict(value) for key, value in (adapter_catalog or DEFAULT_ADAPTER_CATALOG).items()}
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

