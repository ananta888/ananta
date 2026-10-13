"""Apply current Hub memory policy to result-memory persistence and retrieval."""

from __future__ import annotations

import math
from dataclasses import asdict
from typing import Protocol

from agent.services.memory_policy_gateway import MemoryAccessError, MemoryPolicyDecision
from agent.services.memory_provenance_service import memory_access_origin
from agent.services.memory_sensitivity_bridge import memory_sensitivity
from ananta_contracts.memory_access import (
    MemoryActor,
    MemoryOperation,
    MemoryRetention,
    MemoryScope,
    MemorySecurityMetadata,
    MemorySensitivity,
)


class CurrentMemoryAuthorizationPort(Protocol):
    def authorize_current(self, *, actor: MemoryActor, run_id: str, operation: MemoryOperation,
                          channel_id: str, security: MemorySecurityMetadata) -> MemoryPolicyDecision: ...


class ResultMemoryAccess:
    """Keep authorization/namespace concerns outside the result compaction service."""

    def __init__(self, gateway: CurrentMemoryAuthorizationPort):
        self._gateway = gateway

    def authorize_write(self, *, actor, run_id, task_id, goal_id, scope, policy):
        aliases = {"session": "run", "project": "workspace"}
        requested = scope or policy["default_memory_scope"]
        if not isinstance(requested, str) or requested not in policy["allowed_scopes"]:
            raise MemoryAccessError("memory_scope_not_allowed")
        ttl = policy["default_ttl_seconds"]
        if ttl is not None and not math.isfinite(ttl):
            raise MemoryAccessError("memory_retention_invalid")
        security = MemorySecurityMetadata(
            scope=MemoryScope(aliases.get(requested, requested)),
            sensitivity=memory_sensitivity(policy["sensitivity"]),
            retention_class=MemoryRetention.ttl if ttl else MemoryRetention.task,
        )
        decision = self._authorize(actor, run_id, MemoryOperation.append, security)
        if decision.target.namespace.task_id != task_id or decision.target.namespace.goal_id != goal_id:
            raise MemoryAccessError("memory_result_binding_mismatch")
        return decision

    def authorize_read(self, *, actor, run_id, scope=MemoryScope.task, sensitivity=MemorySensitivity.internal):
        security = MemorySecurityMetadata(scope=scope, sensitivity=sensitivity)
        return self._authorize(actor, run_id, MemoryOperation.read, security)

    def _authorize(self, actor, run_id, operation, security):
        if actor is None or run_id is None:
            from agent.services.memory_access_context import current_memory_access

            context = current_memory_access()
            if actor is not None and actor != context.actor or run_id is not None and run_id != context.run_id:
                raise MemoryAccessError("memory_result_binding_mismatch")
            actor, run_id = context.actor, context.run_id
        return self._gateway.authorize_current(
            actor=actor, run_id=run_id, operation=operation,
            channel_id="hub-result-memory", security=security,
        )

    def recheck(self, previous):
        current = self._authorize(previous.actor, previous.run_id, previous.operation, previous.target.security)
        if current.policy_digest != previous.policy_digest or current.target != previous.target:
            raise MemoryAccessError("memory_policy_changed")

    @staticmethod
    def metadata(decision) -> dict:
        return {
            "memory_namespace": asdict(decision.target.namespace),
            "memory_security": decision.target.security.to_mapping(),
            "memory_security_schema": "ananta.memory-security.v1",
            "memory_policy_digest": decision.policy_digest,
            "memory_policy_revision": decision.policy_revision,
            "memory_provenance": memory_access_origin(decision),
        }
