from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from agent.services.mcp_tool_registry import MCPToolRegistry


class MCPReadonlyAdapter:
    """Read-only MCP execution bridge with explicit capability/policy gates."""

    def __init__(
        self,
        *,
        registry: MCPToolRegistry,
        dispatcher: Callable[[str, dict[str, Any]], dict[str, Any]],
        capability_gate: Callable[[str], bool],
        policy_gate: Callable[[str], bool],
    ) -> None:
        self._registry = registry
        self._dispatcher = dispatcher
        self._capability_gate = capability_gate
        self._policy_gate = policy_gate

    def execute(
        self,
        *,
        tool_id: str,
        arguments: dict[str, Any] | None = None,
        capability: str = "mcp.readonly.execute",
        policy_key: str = "mcp_readonly_enabled",
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        if not self._capability_gate(capability):
            raise PermissionError("capability_denied")
        if not self._policy_gate(policy_key):
            raise PermissionError("policy_denied")

        descriptor = self._registry.get(tool_id)
        if not descriptor:
            raise KeyError("unknown_tool")
        if str(descriptor.get("access_class") or "").lower() != "read":
            raise PermissionError("non_read_tool_forbidden")
        if str(descriptor.get("lifecycle") or "enabled").lower() not in {"enabled", "degraded"}:
            raise RuntimeError("tool_disabled")

        normalized_arguments = dict(arguments or {})
        raw_result = self._dispatcher(tool_id, normalized_arguments)
        return {
            "status": "ok",
            "tool_id": tool_id,
            "content": dict(raw_result),
            "artifact": {
                "type": "mcp_readonly_result",
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "trace_id": trace_id,
                "provenance": {
                    "adapter": "mcp_readonly_adapter",
                    "tool_class": str(descriptor.get("access_class") or "read"),
                    "risk_class": str(descriptor.get("risk_class") or "unknown"),
                    "capability": capability,
                    "policy_key": policy_key,
                    "descriptor_scope": list(descriptor.get("allowed_scopes") or []),
                },
            },
        }

    def health(self) -> dict[str, Any]:
        registry_health = self._registry.health()
        status = "healthy" if registry_health.get("status") == "healthy" else "degraded"
        return {
            "status": status,
            "registry": registry_health,
            "mode": "read_only",
        }

