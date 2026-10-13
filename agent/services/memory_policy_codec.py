"""Closed persisted policy codec; no caller-supplied grant admission."""

from dataclasses import asdict

from ananta_contracts.memory_access import (
    MemoryActor,
    MemoryCapability,
    MemoryNamespace,
    MemoryOperation,
    MemoryRunPolicy,
    MemoryScope,
    MemorySensitivity,
)


def encode_policy(policy: MemoryRunPolicy) -> dict:
    return asdict(policy)


def decode_policy(value: dict) -> MemoryRunPolicy:
    fields = {"actor", "namespace", "policy_revision", "expires_at", "active", "capabilities", "trace_id"}
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError("memory_persisted_policy_invalid")
    grants = value["capabilities"]
    if not isinstance(grants, (list, tuple)) or len(grants) > 32:
        raise ValueError("memory_persisted_policy_invalid")
    capabilities = []
    for grant in grants:
        if not isinstance(grant, dict) or set(grant) != {
            "channel_id", "namespace", "scope", "operations", "sensitivity_ceiling", "expires_at",
        }:
            raise ValueError("memory_persisted_policy_invalid")
        operations = grant["operations"]
        if not isinstance(operations, (list, tuple)) or not 1 <= len(operations) <= 5:
            raise ValueError("memory_persisted_policy_invalid")
        capabilities.append(MemoryCapability(
            channel_id=grant["channel_id"], namespace=MemoryNamespace(**grant["namespace"]),
            scope=MemoryScope(grant["scope"]), operations=tuple(MemoryOperation(op) for op in operations),
            sensitivity_ceiling=MemorySensitivity(grant["sensitivity_ceiling"]), expires_at=grant["expires_at"],
        ))
    return MemoryRunPolicy(
        actor=MemoryActor(**value["actor"]), namespace=MemoryNamespace(**value["namespace"]),
        policy_revision=value["policy_revision"], expires_at=value["expires_at"], active=value["active"],
        capabilities=tuple(capabilities), trace_id=value["trace_id"],
    )
