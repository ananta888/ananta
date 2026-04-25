from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

_DEFAULT_SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schemas" / "mcp" / "mcp_tool_descriptor.v1.json"


class MCPToolRegistry:
    """Descriptor-backed MCP tool registry with safe default-deny behavior."""

    def __init__(self, *, schema_path: Path | None = None) -> None:
        self._schema_path = schema_path or _DEFAULT_SCHEMA_PATH
        self._validator = Draft202012Validator(self._load_schema(self._schema_path))
        self._descriptors: dict[str, dict[str, Any]] = {}
        self._load_errors: list[str] = []

    def register(self, descriptor: dict[str, Any]) -> None:
        self._validate_descriptor(descriptor)
        tool_id = str(descriptor.get("tool_id") or "").strip()
        if tool_id in self._descriptors:
            raise ValueError(f"duplicate_tool:{tool_id}")
        access_class = str(descriptor.get("access_class") or "").strip().lower()
        default_enabled = bool(descriptor.get("default_enabled", False))
        if default_enabled and access_class in {"write", "admin"}:
            raise ValueError(f"unsafe_default_enabled:{tool_id}")
        normalized = dict(descriptor)
        normalized["tool_id"] = tool_id
        normalized["access_class"] = access_class
        normalized["risk_class"] = str(descriptor.get("risk_class") or "").strip().lower()
        normalized["lifecycle"] = str(descriptor.get("lifecycle") or "enabled").strip().lower() or "enabled"
        normalized["allowed_scopes"] = [str(item).strip() for item in list(descriptor.get("allowed_scopes") or []) if str(item).strip()]
        self._descriptors[tool_id] = normalized

    def register_many(self, descriptors: list[dict[str, Any]]) -> None:
        for descriptor in descriptors:
            self.register(descriptor)

    def get(self, tool_id: str) -> dict[str, Any] | None:
        key = str(tool_id or "").strip()
        descriptor = self._descriptors.get(key)
        return dict(descriptor) if isinstance(descriptor, dict) else None

    def list_descriptors(self) -> list[dict[str, Any]]:
        return [dict(self._descriptors[key]) for key in sorted(self._descriptors)]

    def is_tool_available(self, tool_id: str, *, scope: str | None = None) -> bool:
        descriptor = self.get(tool_id)
        if not descriptor:
            return False
        if str(descriptor.get("lifecycle") or "enabled") not in {"enabled", "degraded"}:
            return False
        if scope:
            allowed_scopes = set(str(item).strip() for item in list(descriptor.get("allowed_scopes") or []) if str(item).strip())
            if scope not in allowed_scopes:
                return False
        return True

    def health(self) -> dict[str, Any]:
        descriptor_states = [
            str(item.get("lifecycle") or "enabled")
            for item in self._descriptors.values()
            if isinstance(item, dict)
        ]
        degraded_count = sum(1 for state in descriptor_states if state == "degraded")
        disabled_count = sum(1 for state in descriptor_states if state == "disabled")
        status = "healthy"
        reasons: list[str] = []
        if self._load_errors:
            status = "degraded"
            reasons.extend(self._load_errors)
        if degraded_count > 0:
            status = "degraded"
            reasons.append("descriptor_lifecycle_degraded")
        if disabled_count > 0 and status == "healthy":
            status = "degraded"
            reasons.append("descriptor_lifecycle_disabled")
        return {
            "status": status,
            "total_descriptors": len(self._descriptors),
            "degraded_descriptors": degraded_count,
            "disabled_descriptors": disabled_count,
            "load_errors": list(self._load_errors),
            "reasons": reasons,
        }

    def _validate_descriptor(self, descriptor: dict[str, Any]) -> None:
        errors = sorted(self._validator.iter_errors(descriptor), key=lambda err: list(err.path))
        if errors:
            message = "; ".join(
                f"{'.'.join(str(part) for part in err.path) or '<root>'}:{err.message}" for err in errors
            )
            raise ValueError(f"invalid_descriptor:{message}")

    @staticmethod
    def _load_schema(schema_path: Path) -> dict[str, Any]:
        return json.loads(schema_path.read_text(encoding="utf-8"))


mcp_tool_registry = MCPToolRegistry()


def get_mcp_tool_registry() -> MCPToolRegistry:
    return mcp_tool_registry

