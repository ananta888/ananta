from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from agent.services.operation_registry_service import (
    OperationDescriptor,
    OperationRegistryPort,
    get_operation_registry_service,
)

_AUTH_SOURCES = frozenset({"agent_auth", "user_jwt"})
_TRANSPORTS = frozenset({"mcp.tool", "mcp.resource", "api"})
_ACCESS_CLASSES = frozenset({"read", "write", "admin"})
_RISK_CLASSES = frozenset({"low", "medium", "high", "critical"})
_POLICY_HASH_FIELDS = (
    "schema_version",
    "enabled",
    "enforced_transports",
    "allow_operations",
    "deny_operations",
    "allow_groups",
    "deny_groups",
    "allowed_auth_sources",
    "require_admin_for_access_classes",
    "require_approval_for_risks",
    "emit_audit_events",
)


class OperationPolicyConfigError(ValueError):
    def __init__(self, reason_code: str, *, field: str | None = None) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.field = field


@dataclass(frozen=True)
class OperationAuthContext:
    auth_source: str
    is_admin: bool = False
    approval_granted: bool = False


@dataclass(frozen=True)
class OperationPolicyDecision:
    allowed: bool
    reason_code: str
    matched_rule_id: str
    operation_id: str | None
    transport: str | None
    access_class: str | None
    risk_class: str | None
    lifecycle: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reason_code": self.reason_code,
            "matched_rule_id": self.matched_rule_id,
            "operation_id": self.operation_id,
            "transport": self.transport,
            "access_class": self.access_class,
            "risk_class": self.risk_class,
            "lifecycle": self.lifecycle,
        }


class OperationPolicyService:
    """Pure fail-closed evaluator over a registry abstraction."""

    def __init__(self, registry: OperationRegistryPort) -> None:
        self._registry = registry

    @staticmethod
    def _bool(raw: dict[str, Any], key: str, default: bool) -> bool:
        value = raw.get(key, default)
        if not isinstance(value, bool):
            raise OperationPolicyConfigError("operation_policy_boolean_required", field=key)
        return value

    @staticmethod
    def _revision(raw: dict[str, Any]) -> int:
        value = raw.get("revision", 0)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise OperationPolicyConfigError("operation_policy_revision_invalid", field="revision")
        return value

    @staticmethod
    def _string_list(raw: dict[str, Any], key: str, default: list[str]) -> list[str]:
        value = raw.get(key, default)
        if not isinstance(value, list):
            raise OperationPolicyConfigError("operation_policy_list_required", field=key)
        normalized: list[str] = []
        for item in value:
            if not isinstance(item, str) or not item or item != item.strip():
                raise OperationPolicyConfigError("operation_policy_string_id_invalid", field=key)
            normalized.append(item)
        return sorted(set(normalized))

    @staticmethod
    def _policy_hash(policy: dict[str, Any]) -> str:
        payload = {key: policy.get(key) for key in _POLICY_HASH_FIELDS}
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def legacy_mcp_policy(self) -> dict[str, Any]:
        policy = {
            "schema_version": "1.0",
            "enabled": True,
            "revision": 0,
            "enforced_transports": ["mcp.resource", "mcp.tool"],
            "allow_operations": [],
            "deny_operations": [],
            "allow_groups": ["mcp.read.v1"],
            "deny_groups": [],
            "allowed_auth_sources": ["agent_auth", "user_jwt"],
            "require_admin_for_access_classes": ["admin", "write"],
            "require_approval_for_risks": ["critical", "high"],
            "emit_audit_events": True,
            "migration_source": "legacy_mcp_read_only_v1",
        }
        policy["policy_hash"] = self._policy_hash(policy)
        return policy

    def invalid_config_policy(self, reason_code: str) -> dict[str, Any]:
        policy = {
            "schema_version": "1.0",
            "enabled": True,
            "revision": 0,
            "enforced_transports": sorted(_TRANSPORTS),
            "allow_operations": [],
            "deny_operations": [],
            "allow_groups": [],
            "deny_groups": [],
            "allowed_auth_sources": sorted(_AUTH_SOURCES),
            "require_admin_for_access_classes": sorted(_ACCESS_CLASSES),
            "require_approval_for_risks": sorted(_RISK_CLASSES),
            "emit_audit_events": True,
            "migration_source": "invalid_config_fail_closed",
            "configuration_error": reason_code,
        }
        policy["policy_hash"] = self._policy_hash(policy)
        return policy

    def normalize_policy(self, raw: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raise OperationPolicyConfigError("operation_policy_object_required")
        schema_version = raw.get("schema_version", "1.0")
        if schema_version != "1.0":
            raise OperationPolicyConfigError("operation_policy_schema_version_unsupported", field="schema_version")
        enabled = self._bool(raw, "enabled", True)
        enforced_transports = self._string_list(
            raw, "enforced_transports", ["mcp.resource", "mcp.tool"]
        )
        allow_operations = self._string_list(raw, "allow_operations", [])
        deny_operations = self._string_list(raw, "deny_operations", [])
        allow_groups = self._string_list(raw, "allow_groups", [])
        deny_groups = self._string_list(raw, "deny_groups", [])
        allowed_auth_sources = self._string_list(
            raw, "allowed_auth_sources", ["agent_auth", "user_jwt"]
        )
        admin_access = self._string_list(
            raw, "require_admin_for_access_classes", ["admin", "write"]
        )
        approval_risks = self._string_list(
            raw, "require_approval_for_risks", ["critical", "high"]
        )

        for transport in enforced_transports:
            if transport not in _TRANSPORTS:
                raise OperationPolicyConfigError("operation_policy_transport_unknown", field="enforced_transports")
        for auth_source in allowed_auth_sources:
            if auth_source not in _AUTH_SOURCES:
                raise OperationPolicyConfigError("operation_policy_auth_source_unknown", field="allowed_auth_sources")
        for access_class in admin_access:
            if access_class not in _ACCESS_CLASSES:
                raise OperationPolicyConfigError(
                    "operation_policy_access_class_unknown", field="require_admin_for_access_classes"
                )
        for risk_class in approval_risks:
            if risk_class not in _RISK_CLASSES:
                raise OperationPolicyConfigError(
                    "operation_policy_risk_class_unknown", field="require_approval_for_risks"
                )
        for field, operation_ids in (
            ("allow_operations", allow_operations),
            ("deny_operations", deny_operations),
        ):
            for operation_id in operation_ids:
                if self._registry.get(operation_id) is None:
                    raise OperationPolicyConfigError("operation_policy_operation_unknown", field=field)
        for field, group_ids in (("allow_groups", allow_groups), ("deny_groups", deny_groups)):
            for group_id in group_ids:
                if self._registry.group_members(group_id) is None:
                    raise OperationPolicyConfigError("operation_policy_group_unknown", field=field)
        if set(allow_operations) & set(deny_operations):
            raise OperationPolicyConfigError("operation_policy_operation_conflict")
        if set(allow_groups) & set(deny_groups):
            raise OperationPolicyConfigError("operation_policy_group_conflict")
        if enabled and not enforced_transports:
            raise OperationPolicyConfigError("operation_policy_enforced_transports_empty")
        if enabled and not (allow_operations or allow_groups):
            raise OperationPolicyConfigError("operation_policy_allowlist_empty")
        if enabled and not allowed_auth_sources:
            raise OperationPolicyConfigError("operation_policy_auth_sources_empty")

        policy = {
            "schema_version": "1.0",
            "enabled": enabled,
            "revision": self._revision(raw),
            "enforced_transports": enforced_transports,
            "allow_operations": allow_operations,
            "deny_operations": deny_operations,
            "allow_groups": allow_groups,
            "deny_groups": deny_groups,
            "allowed_auth_sources": allowed_auth_sources,
            "require_admin_for_access_classes": admin_access,
            "require_approval_for_risks": approval_risks,
            "emit_audit_events": self._bool(raw, "emit_audit_events", True),
            "migration_source": "explicit_operation_policy",
        }
        policy["policy_hash"] = self._policy_hash(policy)
        return policy

    def resolve_policy(self, cfg: dict[str, Any] | None) -> dict[str, Any]:
        cfg = cfg if isinstance(cfg, dict) else {}
        if "operation_policy" not in cfg:
            return self.legacy_mcp_policy()
        raw = cfg.get("operation_policy")
        if not isinstance(raw, dict):
            return self.invalid_config_policy("operation_policy_object_required")
        try:
            policy = self.normalize_policy(raw)
        except OperationPolicyConfigError as exc:
            return self.invalid_config_policy(exc.reason_code)
        if isinstance(raw.get("_history"), list):
            policy["_history"] = [dict(item) for item in raw["_history"] if isinstance(item, dict)]
        return policy

    def public_projection(self, policy: dict[str, Any], *, include_history: bool = False) -> dict[str, Any]:
        projection = {
            key: policy.get(key)
            for key in (
                "schema_version",
                "enabled",
                "revision",
                "enforced_transports",
                "allow_operations",
                "deny_operations",
                "allow_groups",
                "deny_groups",
                "allowed_auth_sources",
                "require_admin_for_access_classes",
                "require_approval_for_risks",
                "emit_audit_events",
                "migration_source",
                "policy_hash",
                "configuration_error",
            )
        }
        for key in (
            "enforced_transports",
            "allow_operations",
            "deny_operations",
            "allow_groups",
            "deny_groups",
            "allowed_auth_sources",
            "require_admin_for_access_classes",
            "require_approval_for_risks",
        ):
            projection[key] = list(projection.get(key) or [])
        history = policy.get("_history") if isinstance(policy.get("_history"), list) else []
        projection["history_count"] = len(history)
        if include_history:
            projection["history"] = [
                {
                    "revision": item.get("revision"),
                    "policy_hash": item.get("policy_hash"),
                    "replaced_by_revision": item.get("replaced_by_revision"),
                    "changed_at": item.get("changed_at"),
                    "actor": item.get("actor"),
                    "change_kind": item.get("change_kind"),
                    "diff": dict(item.get("diff") or {}),
                }
                for item in history
            ]
        return projection

    def capability_projection(self, policy: dict[str, Any]) -> dict[str, Any]:
        return {
            "schema_version": policy.get("schema_version"),
            "enabled": bool(policy.get("enabled")),
            "revision": int(policy.get("revision") or 0),
            "enforced_transports": list(policy.get("enforced_transports") or []),
            "migration_source": policy.get("migration_source"),
            "policy_hash": policy.get("policy_hash"),
            "configuration_error": policy.get("configuration_error"),
        }

    def decide(
        self,
        descriptor: OperationDescriptor | None,
        policy: dict[str, Any],
        auth: OperationAuthContext,
    ) -> OperationPolicyDecision:
        if descriptor is None:
            return OperationPolicyDecision(False, "unknown_operation", "registry:unknown", None, None, None, None, None)

        base = {
            "operation_id": descriptor.operation_id,
            "transport": descriptor.transport,
            "access_class": descriptor.access_class,
            "risk_class": descriptor.risk_class,
            "lifecycle": descriptor.lifecycle,
        }

        def decision(allowed: bool, reason_code: str, matched_rule_id: str) -> OperationPolicyDecision:
            return OperationPolicyDecision(allowed, reason_code, matched_rule_id, **base)

        if descriptor.lifecycle == "disabled":
            return decision(False, "operation_lifecycle_disabled", "lifecycle:disabled")
        enforced = bool(policy.get("enabled")) and descriptor.transport in set(policy.get("enforced_transports") or [])
        if not enforced:
            return decision(True, "transport_rollout_disabled", f"rollout:disabled:{descriptor.transport}")
        if auth.auth_source not in set(policy.get("allowed_auth_sources") or []):
            return decision(False, "operation_auth_source_denied", f"auth:source:{auth.auth_source or 'unknown'}")

        group_ids = tuple(
            group_id
            for group_id in sorted(set(policy.get("deny_groups") or []))
            if descriptor.operation_id in set(self._registry.group_members(group_id) or ())
        )
        if descriptor.operation_id in set(policy.get("deny_operations") or []):
            return decision(False, "operation_explicitly_denied", f"deny:operation:{descriptor.operation_id}")
        if group_ids:
            return decision(False, "operation_group_denied", f"deny:group:{group_ids[0]}")

        matched_allow = None
        if descriptor.operation_id in set(policy.get("allow_operations") or []):
            matched_allow = f"allow:operation:{descriptor.operation_id}"
        else:
            for group_id in sorted(set(policy.get("allow_groups") or [])):
                if descriptor.operation_id in set(self._registry.group_members(group_id) or ()):
                    matched_allow = f"allow:group:{group_id}"
                    break
        if matched_allow is None:
            return decision(False, "operation_not_allowlisted", "default:deny")
        if descriptor.access_class in set(policy.get("require_admin_for_access_classes") or []) and not auth.is_admin:
            return decision(False, "operation_admin_required", f"access:admin:{descriptor.access_class}")
        if descriptor.risk_class in set(policy.get("require_approval_for_risks") or []) and not auth.approval_granted:
            return decision(False, "operation_approval_required", f"risk:approval:{descriptor.risk_class}")
        reason = "operation_allowed_degraded" if descriptor.lifecycle == "degraded" else "operation_allowed"
        return decision(True, reason, matched_allow)


operation_policy_service = OperationPolicyService(get_operation_registry_service())


def get_operation_policy_service() -> OperationPolicyService:
    return operation_policy_service
