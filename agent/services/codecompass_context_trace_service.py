"""CodeCompassContextTraceService — TRANS-003

Erzeugt und verwaltet ContextTrace-Daten fuer CodeCompass und externe Provider.
Erklaert welche Query, welcher Provider, welcher Treffer, welche Policy und
welcher Auswahlgrund beteiligt war.
"""
from __future__ import annotations
import time, uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

class ContextDiscardReason(str, Enum):
    DENIED_PATH = "denied_path"
    DUPLICATE = "duplicate"
    STALE = "stale"
    LOW_SCORE = "low_score"
    OVER_BUDGET = "over_budget"
    EXTERNAL_PROVIDER_BLOCKED = "external_provider_blocked"
    REDACTED = "redacted"

@dataclass
class ContextHitRecord:
    hit_id: str
    provider: str          # "codecompass" | "augment" | "manual"
    path: str
    symbol: str | None
    score: float
    source_kind: str
    confidence: float
    policy_status: str
    is_external: bool
    freshness: float
    snippet_truncated: bool

@dataclass
class DiscardedHitRecord:
    hit_id: str
    provider: str
    path: str
    score: float
    reason: ContextDiscardReason
    policy_note: str | None

@dataclass
class CodeCompassContextTrace:
    trace_id: str
    run_id: str
    tool_call_id: str | None
    query: str
    provider: str
    selected_items: list[ContextHitRecord]
    discarded_items: list[DiscardedHitRecord]
    budget_chars_used: int
    budget_chars_limit: int
    policy_decisions: list[str]
    created_at: float

    def external_items_count(self) -> int:
        return sum(1 for i in self.selected_items if i.is_external)

    def has_external_evidence(self) -> bool:
        return any(i.is_external for i in self.selected_items)

    def short_summary(self) -> str:
        discard_counts: dict[str, int] = {}
        for d in self.discarded_items:
            discard_counts[d.reason.value] = discard_counts.get(d.reason.value, 0) + 1
        discard_str = ", ".join(f"{k}: {v}" for k, v in discard_counts.items()) if discard_counts else "none"
        return (f"{self.provider}: {len(self.selected_items)} treffer, "
                f"{len(self.discarded_items)} verworfen ({discard_str})")

    def as_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "run_id": self.run_id,
            "tool_call_id": self.tool_call_id,
            "query": self.query,
            "provider": self.provider,
            "selected_count": len(self.selected_items),
            "discarded_count": len(self.discarded_items),
            "budget_chars_used": self.budget_chars_used,
            "budget_chars_limit": self.budget_chars_limit,
            "policy_decisions": self.policy_decisions,
            "external_items_count": self.external_items_count(),
            "created_at": self.created_at,
        }


class CodeCompassContextTraceService:
    def __init__(self) -> None:
        self._pending: dict[str, dict] = {}
        self._completed: dict[str, CodeCompassContextTrace] = {}

    def start_trace(self, *, run_id: str, query: str, provider: str = "codecompass",
                    tool_call_id: str | None = None, budget_chars_limit: int = 40000) -> str:
        trace_id = str(uuid.uuid4())
        self._pending[trace_id] = {
            "run_id": run_id, "query": query, "provider": provider,
            "tool_call_id": tool_call_id, "budget_chars_limit": budget_chars_limit,
            "selected": [], "discarded": [], "started_at": time.time(),
        }
        return trace_id

    def add_selected(self, trace_id: str, *, provider: str, path: str, score: float,
                     symbol: str | None = None, source_kind: str = "unknown",
                     confidence: float = 0.5, policy_status: str = "allowed",
                     is_external: bool = False, freshness: float = 1.0,
                     snippet_chars: int = 0, snippet_truncated: bool = False) -> None:
        if trace_id not in self._pending:
            return
        hit_id = str(uuid.uuid4())[:8]
        self._pending[trace_id]["selected"].append(ContextHitRecord(
            hit_id=hit_id, provider=provider, path=path, symbol=symbol,
            score=score, source_kind=source_kind, confidence=confidence,
            policy_status=policy_status, is_external=is_external,
            freshness=freshness, snippet_truncated=snippet_truncated,
        ))

    def add_discarded(self, trace_id: str, *, provider: str, path: str, score: float,
                      reason: ContextDiscardReason, policy_note: str | None = None) -> None:
        if trace_id not in self._pending:
            return
        hit_id = str(uuid.uuid4())[:8]
        self._pending[trace_id]["discarded"].append(DiscardedHitRecord(
            hit_id=hit_id, provider=provider, path=path, score=score,
            reason=reason, policy_note=policy_note,
        ))

    def finalize(self, trace_id: str, *, budget_chars_used: int = 0,
                 policy_decisions: list[str] | None = None) -> CodeCompassContextTrace:
        p = self._pending.pop(trace_id, None)
        if p is None:
            raise KeyError(f"Unknown trace_id: {trace_id}")
        trace = CodeCompassContextTrace(
            trace_id=trace_id,
            run_id=p["run_id"],
            tool_call_id=p["tool_call_id"],
            query=p["query"],
            provider=p["provider"],
            selected_items=p["selected"],
            discarded_items=p["discarded"],
            budget_chars_used=budget_chars_used,
            budget_chars_limit=p["budget_chars_limit"],
            policy_decisions=list(policy_decisions or []),
            created_at=time.time(),
        )
        self._completed[trace_id] = trace
        return trace

    def get(self, trace_id: str) -> CodeCompassContextTrace | None:
        return self._completed.get(trace_id)
