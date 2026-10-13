"""Closed memory policy DTOs. Content never becomes an instruction or authority."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from enum import Enum


class MemoryContractError(ValueError):
    pass


class MemoryScope(str, Enum):
    run = "run"
    task = "task"
    goal = "goal"
    conversation = "conversation"
    workspace = "workspace"
    global_ = "global"


class MemoryOperation(str, Enum):
    read = "read"
    append = "append"
    update = "update"
    delete = "delete"
    promote = "promote"


class MemorySensitivity(str, Enum):
    public = "public"
    internal = "internal"
    internal_high = "internal_high"
    secret = "secret"
    credential = "credential"
    security_sensitive = "security_sensitive"


class MemoryRetention(str, Enum):
    ephemeral = "ephemeral"
    task = "task"
    ttl = "ttl"
    durable = "durable"


class MemoryTrust(str, Enum):
    untrusted = "untrusted"
    user_confirmed = "user_confirmed"
    source_verified = "source_verified"


def require_memory_identifier(value: str, field: str) -> str:
    if (not isinstance(value, str) or not value or len(value) > 160
            or value != value.strip() or "*" in value or any(ord(c) < 32 for c in value)):
        raise MemoryContractError(f"memory_{field}_invalid")
    try:
        value.encode("utf-8")
    except UnicodeError as exc:
        raise MemoryContractError(f"memory_{field}_invalid") from exc
    return value


def require_memory_timestamp(value: float, field: str) -> None:
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
        raise MemoryContractError(f"memory_{field}_invalid")


@dataclass(frozen=True)
class MemoryNamespace:
    tenant_id: str
    workspace_id: str | None = None
    goal_id: str | None = None
    task_id: str | None = None
    run_id: str | None = None
    conversation_id: str | None = None

    def __post_init__(self):
        require_memory_identifier(self.tenant_id, "tenant_id")
        for field in ("workspace_id", "goal_id", "task_id", "run_id", "conversation_id"):
            value = getattr(self, field)
            if value is not None:
                require_memory_identifier(value, field)

    def key(self, scope: MemoryScope) -> tuple[str, ...]:
        bindings = self.bindings(scope)
        return (scope.value, *(value or "" for value in bindings.values()))

    def bindings(self, scope: MemoryScope) -> dict[str, str | None]:
        if not isinstance(scope, MemoryScope):
            raise MemoryContractError("memory_scope_invalid")
        fields = {
            MemoryScope.run: ("tenant_id", "workspace_id", "task_id", "run_id"),
            MemoryScope.task: ("tenant_id", "workspace_id", "task_id"),
            MemoryScope.goal: ("tenant_id", "workspace_id", "goal_id"),
            MemoryScope.conversation: ("tenant_id", "workspace_id", "conversation_id"),
            MemoryScope.workspace: ("tenant_id", "workspace_id"),
            MemoryScope.global_: ("tenant_id",),
        }[scope]
        values = {field: getattr(self, field) for field in fields}
        if any(value is None for value in values.values()):
            raise MemoryContractError("memory_namespace_incomplete")
        # Include goal binding for task/run scopes, even when there is no goal.
        if scope in (MemoryScope.run, MemoryScope.task):
            values["goal_id"] = self.goal_id
        return values


@dataclass(frozen=True)
class MemorySecurityMetadata:
    scope: MemoryScope = MemoryScope.task
    sensitivity: MemorySensitivity = MemorySensitivity.internal
    retention_class: MemoryRetention = MemoryRetention.task
    trust_level: MemoryTrust = MemoryTrust.untrusted

    def __post_init__(self):
        for field, kind in (("scope", MemoryScope), ("sensitivity", MemorySensitivity),
                            ("retention_class", MemoryRetention), ("trust_level", MemoryTrust)):
            if not isinstance(getattr(self, field), kind):
                raise MemoryContractError(f"memory_{field}_invalid")

    def to_mapping(self) -> dict[str, str]:
        return {"scope": self.scope.value, "sensitivity": self.sensitivity.value,
                "retention_class": self.retention_class.value, "trust_level": self.trust_level.value}

    @classmethod
    def from_mapping(cls, value: dict) -> MemorySecurityMetadata:
        fields = {"scope": MemoryScope, "sensitivity": MemorySensitivity,
                  "retention_class": MemoryRetention, "trust_level": MemoryTrust}
        if not isinstance(value, dict) or set(value) - fields.keys():
            raise MemoryContractError("memory_security_metadata_invalid")
        try:
            return cls(**{key: kind(value[key]) for key, kind in fields.items() if key in value})
        except (TypeError, ValueError) as exc:
            raise MemoryContractError("memory_security_metadata_invalid") from exc


def normalize_legacy_memory_metadata(value: dict | None) -> dict:
    """Idempotent additive metadata migration; legacy approval never implies trust."""
    if value is not None and not isinstance(value, dict):
        raise MemoryContractError("memory_security_metadata_invalid")
    result = dict(value or {})
    if result.get("memory_security_schema") == "ananta.memory-security.v1":
        security = MemorySecurityMetadata.from_mapping(result.get("memory_security"))
    else:
        # Legacy scope labels/approval bits cannot prove a wider namespace or trust.
        sensitivity = result.get("sensitivity", "internal")
        try:
            sensitivity = MemorySensitivity(sensitivity)
        except (TypeError, ValueError):
            sensitivity = MemorySensitivity.internal
        security = MemorySecurityMetadata(sensitivity=sensitivity)
        for field in ("memory_namespace", "memory_policy_digest", "memory_policy_revision"):
            result.pop(field, None)
    result["memory_security"] = security.to_mapping()
    result["memory_security_schema"] = "ananta.memory-security.v1"
    return result


@dataclass(frozen=True)
class MemoryActor:
    tenant_id: str
    subject: str

    def __post_init__(self):
        require_memory_identifier(self.tenant_id, "tenant_id")
        require_memory_identifier(self.subject, "subject")


@dataclass(frozen=True)
class MemoryTarget:
    channel_id: str
    namespace: MemoryNamespace
    security: MemorySecurityMetadata

    def __post_init__(self):
        require_memory_identifier(self.channel_id, "channel_id")
        if not isinstance(self.namespace, MemoryNamespace) or not isinstance(self.security, MemorySecurityMetadata):
            raise MemoryContractError("memory_target_invalid")
        self.namespace.key(self.security.scope)


@dataclass(frozen=True)
class MemoryCapability:
    channel_id: str
    namespace: MemoryNamespace
    scope: MemoryScope
    operations: tuple[MemoryOperation, ...]
    sensitivity_ceiling: MemorySensitivity
    expires_at: float

    def __post_init__(self):
        require_memory_identifier(self.channel_id, "channel_id")
        if not isinstance(self.namespace, MemoryNamespace):
            raise MemoryContractError("memory_capability_namespace_invalid")
        self.namespace.key(self.scope)
        if (not isinstance(self.operations, tuple) or not self.operations or len(self.operations) > 5
                or any(not isinstance(item, MemoryOperation) for item in self.operations)
                or len(set(self.operations)) != len(self.operations)):
            raise MemoryContractError("memory_capability_operations_invalid")
        if not isinstance(self.sensitivity_ceiling, MemorySensitivity):
            raise MemoryContractError("memory_sensitivity_invalid")
        require_memory_timestamp(self.expires_at, "expires_at")


@dataclass(frozen=True)
class MemoryRunPolicy:
    """Server-owned current policy; never accepted from Worker/model request bodies."""

    actor: MemoryActor
    namespace: MemoryNamespace
    policy_revision: str
    expires_at: float
    active: bool = True
    capabilities: tuple[MemoryCapability, ...] = ()
    trace_id: str | None = None

    def __post_init__(self):
        if not isinstance(self.actor, MemoryActor) or not isinstance(self.namespace, MemoryNamespace):
            raise MemoryContractError("memory_run_policy_invalid")
        self.namespace.key(MemoryScope.run)
        if self.actor.tenant_id != self.namespace.tenant_id or type(self.active) is not bool:
            raise MemoryContractError("memory_run_policy_binding_invalid")
        require_memory_identifier(self.policy_revision, "policy_revision")
        require_memory_timestamp(self.expires_at, "expires_at")
        if self.trace_id is not None:
            require_memory_identifier(self.trace_id, "trace_id")
        if (not isinstance(self.capabilities, tuple) or len(self.capabilities) > 32
                or any(not isinstance(item, MemoryCapability) for item in self.capabilities)):
            raise MemoryContractError("memory_capability_manifest_invalid")
        for capability in self.capabilities:
            if capability.namespace.tenant_id != self.namespace.tenant_id or capability.expires_at > self.expires_at:
                raise MemoryContractError("memory_capability_binding_invalid")

    @property
    def digest(self) -> str:
        encoded = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
