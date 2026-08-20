from __future__ import annotations

import threading
from collections import Counter
from typing import Any

from agent.common.audit import log_audit
from agent.services.operation_policy_service import OperationPolicyDecision


class OperationPolicyObservabilityService:
    """Low-cardinality counters plus redacted audit events for policy decisions."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counts: Counter[tuple[str, str, str]] = Counter()

    def record(
        self,
        decision: OperationPolicyDecision,
        *,
        trace_id: str | None,
        surface: str,
        emit_audit_event: bool = True,
    ) -> None:
        transport = decision.transport or "unknown"
        outcome = "allow" if decision.allowed else "deny"
        key = (transport, outcome, decision.reason_code)
        with self._lock:
            self._counts[key] += 1
        if emit_audit_event:
            log_audit(
                "operation_policy_decision",
                {
                    "trace_id": trace_id,
                    "operation_id": decision.operation_id,
                    "transport": transport,
                    "outcome": outcome,
                    "reason_code": decision.reason_code,
                    "rule_id": decision.matched_rule_id,
                    "surface": str(surface or "unknown")[:80],
                },
            )

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            rows = [
                {
                    "transport": transport,
                    "outcome": outcome,
                    "reason_code": reason,
                    "count": count,
                }
                for (transport, outcome, reason), count in sorted(self._counts.items())
            ]
        return {"schema": "ananta.operation_policy_metrics.v1", "items": rows, "count": sum(row["count"] for row in rows)}


operation_policy_observability_service = OperationPolicyObservabilityService()


def get_operation_policy_observability_service() -> OperationPolicyObservabilityService:
    return operation_policy_observability_service
