"""Bounded DMoE audit records without prompts, source text or free-form IDs."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any, Protocol

_EVENTS = frozenset(
    {
        "candidate_search",
        "router_decision",
        "activation",
        "fallback",
        "training",
        "evaluation",
        "revocation",
    }
)
_REASONS = frozenset(
    {
        "expert_selected",
        "expert_unavailable",
        "expert_disabled",
        "scope_denied",
        "runtime_denied",
        "resource_denied",
        "policy_denied",
        "evaluation_failed",
        "completed",
    }
)


class KnowledgeExpertAuditSinkPort(Protocol):
    def emit(self, event: Mapping[str, Any]) -> None: ...


class KnowledgeExpertObserver:
    def __init__(self, sink: KnowledgeExpertAuditSinkPort, *, id_salt: bytes) -> None:
        if len(id_salt) < 16:
            raise ValueError("knowledge_expert_observer_salt_invalid")
        self._sink = sink
        self._salt = bytes(id_salt)

    def record(
        self,
        *,
        event_type: str,
        reason_code: str,
        tenant_id: str,
        mode: str,
        duration_ms: int,
        expert_count: int,
        input_tokens: int,
        output_tokens: int,
        cost_micros: int = 0,
        rag_baseline_cost_micros: int = 0,
    ) -> None:
        if event_type not in _EVENTS or reason_code not in _REASONS:
            raise ValueError("knowledge_expert_observation_dimension_invalid")
        numbers = (
            duration_ms,
            expert_count,
            input_tokens,
            output_tokens,
            cost_micros,
            rag_baseline_cost_micros,
        )
        if any(isinstance(value, bool) or value < 0 for value in numbers):
            raise ValueError("knowledge_expert_observation_value_invalid")
        tenant_hash = hashlib.sha256(self._salt + tenant_id.encode("utf-8")).hexdigest()[:16]
        self._sink.emit(
            {
                "schema": "ananta.knowledge-expert-observation.v1",
                "event_type": event_type,
                "reason_code": reason_code,
                "tenant_bucket": tenant_hash,
                "mode": mode if mode in {"base_only", "rag_only", "expert_only", "expert_plus_rag"} else "unknown",
                "duration_ms": min(duration_ms, 86_400_000),
                "expert_count": min(expert_count, 64),
                "input_tokens": min(input_tokens, 10_000_000),
                "output_tokens": min(output_tokens, 10_000_000),
                "cost_micros": min(cost_micros, 10**15),
                "rag_baseline_cost_micros": min(rag_baseline_cost_micros, 10**15),
            }
        )


__all__ = ["KnowledgeExpertAuditSinkPort", "KnowledgeExpertObserver"]
