from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlparse

from agent.services.mcp_registry_service import get_mcp_registry_service

_OPERATION_ID_RE = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$", re.ASCII)
_GROUP_ID_RE = re.compile(r"^(?:mcp|api)\.[a-z][a-z0-9_]*\.v[1-9][0-9]*$", re.ASCII)
_TRANSPORTS = frozenset({"mcp.tool", "mcp.resource", "api"})
_ACCESS_CLASSES = frozenset({"read", "write", "admin"})
_RISK_CLASSES = frozenset({"low", "medium", "high", "critical"})
_LIFECYCLES = frozenset({"enabled", "degraded", "disabled"})
_HTTP_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"})


class OperationRegistryError(ValueError):
    """Raised when the operation catalog would become ambiguous or unsafe."""


@dataclass(frozen=True)
class OperationDescriptor:
    operation_id: str
    transport: str
    target: str
    access_class: str
    risk_class: str
    lifecycle: str
    description: str
    owner: str
    http_method: str | None = None
    side_effecting: bool = False
    default_enabled: bool = False

    def __post_init__(self) -> None:
        if not _OPERATION_ID_RE.fullmatch(self.operation_id):
            raise OperationRegistryError(f"invalid_operation_id:{self.operation_id}")
        expected_prefix = f"{self.transport}."
        if self.transport not in _TRANSPORTS or not self.operation_id.startswith(expected_prefix):
            raise OperationRegistryError(f"operation_transport_mismatch:{self.operation_id}")
        if not self.target or self.target != self.target.strip():
            raise OperationRegistryError(f"invalid_operation_target:{self.operation_id}")
        if self.access_class not in _ACCESS_CLASSES:
            raise OperationRegistryError(f"invalid_access_class:{self.operation_id}")
        if self.risk_class not in _RISK_CLASSES:
            raise OperationRegistryError(f"invalid_risk_class:{self.operation_id}")
        if self.lifecycle not in _LIFECYCLES:
            raise OperationRegistryError(f"invalid_lifecycle:{self.operation_id}")
        if not self.description.strip() or not self.owner.strip():
            raise OperationRegistryError(f"incomplete_operation_descriptor:{self.operation_id}")
        if self.transport == "api":
            if self.http_method not in _HTTP_METHODS or not self.target.startswith("/"):
                raise OperationRegistryError(f"invalid_api_descriptor:{self.operation_id}")
        elif self.http_method is not None:
            raise OperationRegistryError(f"unexpected_http_method:{self.operation_id}")
        if self.access_class == "read" and self.side_effecting:
            raise OperationRegistryError(f"read_operation_marked_side_effecting:{self.operation_id}")
        if self.access_class in {"write", "admin"} and not self.side_effecting:
            raise OperationRegistryError(f"mutation_missing_side_effect_flag:{self.operation_id}")
        if self.default_enabled and self.access_class in {"write", "admin"}:
            raise OperationRegistryError(f"unsafe_default_enabled:{self.operation_id}")

    def as_dict(self, *, group_ids: tuple[str, ...] = ()) -> dict[str, Any]:
        return {
            "operation_id": self.operation_id,
            "transport": self.transport,
            "name_or_route": self.target,
            "http_method": self.http_method,
            "access_class": self.access_class,
            "risk_class": self.risk_class,
            "lifecycle": self.lifecycle,
            "description": self.description,
            "owner": self.owner,
            "side_effecting": self.side_effecting,
            "default_enabled": self.default_enabled,
            "group_ids": list(group_ids),
        }


class OperationRegistryPort(Protocol):
    def get(self, operation_id: str) -> OperationDescriptor | None: ...

    def get_for_target(
        self,
        *,
        transport: str,
        target: str,
        http_method: str | None = None,
    ) -> OperationDescriptor | None: ...

    def list_descriptors(self) -> tuple[OperationDescriptor, ...]: ...

    def group_members(self, group_id: str) -> tuple[str, ...] | None: ...


class OperationRegistryService:
    """Stable operation catalog only; dispatch and policy stay in separate services."""

    def __init__(self) -> None:
        self._descriptors: dict[str, OperationDescriptor] = {}
        self._targets: dict[tuple[str, str, str | None], str] = {}
        self._groups: dict[str, tuple[str, ...]] = {}

    def register(self, descriptor: OperationDescriptor) -> None:
        self.register_many((descriptor,))

    def register_many(self, descriptors: tuple[OperationDescriptor, ...] | list[OperationDescriptor]) -> None:
        pending_ids = dict(self._descriptors)
        pending_targets = dict(self._targets)
        for descriptor in descriptors:
            if descriptor.operation_id in pending_ids:
                raise OperationRegistryError(f"duplicate_operation_id:{descriptor.operation_id}")
            target_key = (descriptor.transport, descriptor.target, descriptor.http_method)
            if target_key in pending_targets:
                raise OperationRegistryError(f"duplicate_operation_target:{descriptor.operation_id}")
            pending_ids[descriptor.operation_id] = descriptor
            pending_targets[target_key] = descriptor.operation_id
        self._descriptors = pending_ids
        self._targets = pending_targets

    def register_group(self, group_id: str, operation_ids: tuple[str, ...] | list[str]) -> None:
        if not _GROUP_ID_RE.fullmatch(group_id) or group_id in self._groups:
            raise OperationRegistryError(f"invalid_or_duplicate_operation_group:{group_id}")
        members = tuple(sorted(set(operation_ids)))
        if not members:
            raise OperationRegistryError(f"empty_operation_group:{group_id}")
        unknown = [operation_id for operation_id in members if operation_id not in self._descriptors]
        if unknown:
            raise OperationRegistryError(f"unknown_group_operation:{group_id}:{unknown[0]}")
        self._groups[group_id] = members

    def get(self, operation_id: str) -> OperationDescriptor | None:
        if not isinstance(operation_id, str) or operation_id != operation_id.strip():
            return None
        return self._descriptors.get(operation_id)

    def require(self, operation_id: str) -> OperationDescriptor:
        descriptor = self.get(operation_id)
        if descriptor is None:
            raise OperationRegistryError(f"unknown_operation_id:{operation_id}")
        return descriptor

    def get_for_target(
        self,
        *,
        transport: str,
        target: str,
        http_method: str | None = None,
    ) -> OperationDescriptor | None:
        method = str(http_method or "").upper() or None
        operation_id = self._targets.get((str(transport), str(target), method))
        return self._descriptors.get(operation_id) if operation_id else None

    def list_descriptors(self) -> tuple[OperationDescriptor, ...]:
        return tuple(self._descriptors[key] for key in sorted(self._descriptors))

    def group_members(self, group_id: str) -> tuple[str, ...] | None:
        return self._groups.get(group_id)

    def list_groups(self) -> dict[str, tuple[str, ...]]:
        return {key: self._groups[key] for key in sorted(self._groups)}

    def groups_for(self, operation_id: str) -> tuple[str, ...]:
        return tuple(group_id for group_id, members in sorted(self._groups.items()) if operation_id in members)

    def export(self) -> list[dict[str, Any]]:
        return [
            descriptor.as_dict(group_ids=self.groups_for(descriptor.operation_id))
            for descriptor in self.list_descriptors()
        ]


_MCP_READ_TOOL_NAMES_V1 = (
    "artifacts.list",
    "codecompass.analytics_query",
    "codecompass.architecture_expand",
    "codecompass.architecture_intelligence",
    "codecompass.architecture_overview",
    "codecompass.layers_heads",
    "codecompass.layers_plan",
    "codecompass.retrieve",
    "codecompass.rlm_analyze",
    "evolution.proposals.list",
    "evolution.providers.list",
    "health.get",
    "knowledge.list_collections",
    "providers.list_models",
    "tasks.get",
    "tasks.list",
)
_MCP_WRITE_TOOL_NAMES_V1 = (
    "classroom.reanalyze",
    "classroom.transcript_event",
    "evolution.analyze",
)
_MCP_RESOURCE_URIS_V1 = (
    "ananta://artifacts/list",
    "ananta://evolution/providers",
    "ananta://knowledge/collections",
    "ananta://providers/models",
    "ananta://system/health",
    "ananta://tasks/recent",
)
_API_READ_OPERATION_IDS_V1 = (
    "api.config.get",
    "api.governance.operations.get",
    "api.governance.policy.get",
)
_API_ADMIN_OPERATION_IDS_V1 = (
    "api.config.operation_policy.rollback.post",
    "api.config.update.post",
)


def mcp_tool_operation_id(name: str) -> str:
    return f"mcp.tool.{name}"


def mcp_resource_operation_id(uri: str) -> str:
    parsed = urlparse(str(uri))
    if parsed.scheme != "ananta" or not parsed.netloc:
        raise OperationRegistryError(f"invalid_mcp_resource_uri:{uri}")
    segments = [parsed.netloc, *[part for part in parsed.path.split("/") if part]]
    operation_id = "mcp.resource." + ".".join(segments)
    if not _OPERATION_ID_RE.fullmatch(operation_id):
        raise OperationRegistryError(f"invalid_mcp_resource_operation_id:{uri}")
    return operation_id


def _build_default_registry() -> OperationRegistryService:
    registry = OperationRegistryService()
    mcp_registry = get_mcp_registry_service()
    for spec in mcp_registry.tool_specs():
        registry.register(
            OperationDescriptor(
                operation_id=mcp_tool_operation_id(spec.name),
                transport="mcp.tool",
                target=spec.name,
                access_class=spec.access_class,
                risk_class=spec.risk_class,
                lifecycle=spec.lifecycle,
                description=spec.description,
                owner="hub-mcp",
                side_effecting=spec.access_class != "read",
                default_enabled=spec.default_enabled,
            )
        )
    for spec in mcp_registry.resource_specs():
        registry.register(
            OperationDescriptor(
                operation_id=mcp_resource_operation_id(spec.uri),
                transport="mcp.resource",
                target=spec.uri,
                access_class="read",
                risk_class=spec.risk_class,
                lifecycle=spec.lifecycle,
                description=spec.description,
                owner="hub-mcp",
            )
        )
    registry.register_many(
        (
            OperationDescriptor(
                "api.config.get", "api", "/config", "read", "low", "enabled",
                "Read the redacted Hub configuration.", "hub-governance", http_method="GET",
            ),
            OperationDescriptor(
                "api.config.update.post", "api", "/config", "admin", "high", "enabled",
                "Update validated Hub configuration.", "hub-governance", http_method="POST", side_effecting=True,
            ),
            OperationDescriptor(
                "api.config.operation_policy.rollback.post", "api", "/config/operation-policy/rollback",
                "admin", "high", "enabled", "Rollback to a validated operation-policy revision.",
                "hub-governance", http_method="POST", side_effecting=True,
            ),
            OperationDescriptor(
                "api.governance.policy.get", "api", "/governance/policy", "read", "low", "enabled",
                "Read the effective platform-governance policy.", "hub-governance", http_method="GET",
            ),
            OperationDescriptor(
                "api.governance.operations.get", "api", "/governance/operations", "read", "low", "enabled",
                "Read the admin-only operation catalog and effective decisions.", "hub-governance", http_method="GET",
            ),
        )
    )
    registry.register_group(
        "mcp.read.v1",
        tuple(mcp_tool_operation_id(name) for name in _MCP_READ_TOOL_NAMES_V1)
        + tuple(mcp_resource_operation_id(uri) for uri in _MCP_RESOURCE_URIS_V1),
    )
    registry.register_group(
        "mcp.write.v1",
        tuple(mcp_tool_operation_id(name) for name in _MCP_WRITE_TOOL_NAMES_V1),
    )
    registry.register_group("api.read.v1", _API_READ_OPERATION_IDS_V1)
    registry.register_group("api.admin.v1", _API_ADMIN_OPERATION_IDS_V1)
    return registry


operation_registry_service = _build_default_registry()


def get_operation_registry_service() -> OperationRegistryService:
    return operation_registry_service
