"""Record bounded policy facts, never raw memory content or private reasoning."""

from __future__ import annotations

import time
from dataclasses import asdict
from typing import Callable, Protocol

from agent.services.memory_policy_gateway import MemoryPolicyDecision
from ananta_contracts.memory_access import require_memory_timestamp


class MemoryAuditWritePort(Protocol):
    def append(self, payload: dict, *, created_at: float, retention_until: float) -> dict | None: ...


class MemoryAuditService:
    """Audit retention is independent of the lifetime of the underlying content."""

    def __init__(self, *, repository: MemoryAuditWritePort, retention_seconds: float = 90 * 86400,
                 clock: Callable[[], float] = time.time):
        require_memory_timestamp(retention_seconds, "audit_retention")
        if not 3600 <= retention_seconds <= 365 * 86400:
            raise ValueError("memory_audit_retention_invalid")
        self._repository = repository
        self._retention = retention_seconds
        self._clock = clock

    def record(self, decision: MemoryPolicyDecision) -> dict | None:
        if not isinstance(decision, MemoryPolicyDecision):
            raise ValueError("memory_audit_decision_invalid")
        now = self._clock()
        require_memory_timestamp(now, "audit_clock")
        payload = {
            "schema_version": "ananta.memory-audit-decision.v1",
            "actor": asdict(decision.actor), "run_id": decision.run_id,
            "operation": decision.operation.value, "allowed": decision.allowed,
            "rule_id": decision.rule_id, "reason_code": decision.reason_code,
            "policy_revision": decision.policy_revision, "policy_digest": decision.policy_digest,
            "trace_id": decision.trace_id, "target": asdict(decision.target) if decision.target else None,
            "event_kind": "policy_decision",
        }
        return self._repository.append(payload, created_at=now, retention_until=now + self._retention)
