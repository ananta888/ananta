"""DelegationTraceService — TRANS-005

Warum wurde welcher Worker/Expert gewählt?
Jede Delegation erzeugt einen unveränderlichen DelegationTrace.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

# Valid reasons for not choosing a worker candidate
REASON_NOT_CHOSEN_VALUES = frozenset(
    (
        "missing_capability",
        "denied_provider",
        "too_risky",
        "unavailable",
        "lower_score",
    )
)


@dataclass
class WorkerAlternative:
    worker_id: str
    reason_not_chosen: str   # "missing_capability" | "denied_provider" | "too_risky" | "unavailable" | "lower_score"
    score: float | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "worker_id": self.worker_id,
            "reason_not_chosen": self.reason_not_chosen,
            "score": self.score,
        }


@dataclass
class DelegationTrace:
    trace_id: str
    run_id: str
    goal_summary: str
    chosen_worker_id: str
    chosen_expert_id: str | None
    selection_reason: str
    policy_scope_id: str | None
    context_provided: list[str]        # artifact_ids passed to the worker
    tools_granted: list[str]           # always explicit, never implicit
    alternatives_considered: list[WorkerAlternative]
    created_at: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "run_id": self.run_id,
            "goal_summary": self.goal_summary,
            "chosen_worker_id": self.chosen_worker_id,
            "chosen_expert_id": self.chosen_expert_id,
            "selection_reason": self.selection_reason,
            "policy_scope_id": self.policy_scope_id,
            "context_provided": list(self.context_provided),
            "tools_granted": list(self.tools_granted),
            "alternatives_considered": [a.as_dict() for a in self.alternatives_considered],
            "created_at": self.created_at,
        }


def _build_alternative(alt: dict[str, Any]) -> WorkerAlternative:
    reason = str(alt.get("reason_not_chosen") or "unavailable")
    if reason not in REASON_NOT_CHOSEN_VALUES:
        reason = "unavailable"
    score_raw = alt.get("score")
    score: float | None = float(score_raw) if score_raw is not None else None
    return WorkerAlternative(
        worker_id=str(alt.get("worker_id") or ""),
        reason_not_chosen=reason,
        score=score,
    )


class DelegationTraceService:
    """Records worker-selection decisions with full rationale."""

    def record(
        self,
        *,
        run_id: str,
        goal_summary: str,
        chosen_worker_id: str,
        selection_reason: str,
        chosen_expert_id: str | None = None,
        policy_scope_id: str | None = None,
        context_provided: list[str] | None = None,
        tools_granted: list[str] | None = None,
        alternatives: list[dict] | None = None,
    ) -> DelegationTrace:
        """Create a DelegationTrace."""
        alts: list[WorkerAlternative] = []
        for alt in (alternatives or []):
            if isinstance(alt, dict):
                alts.append(_build_alternative(alt))
            elif isinstance(alt, WorkerAlternative):
                alts.append(alt)

        return DelegationTrace(
            trace_id=str(uuid.uuid4()),
            run_id=str(run_id or ""),
            goal_summary=str(goal_summary or ""),
            chosen_worker_id=str(chosen_worker_id or ""),
            chosen_expert_id=str(chosen_expert_id) if chosen_expert_id else None,
            selection_reason=str(selection_reason or ""),
            policy_scope_id=str(policy_scope_id) if policy_scope_id else None,
            context_provided=list(context_provided) if context_provided else [],
            tools_granted=list(tools_granted) if tools_granted is not None else [],
            alternatives_considered=alts,
            created_at=time.time(),
        )

    def to_dict(self, trace: DelegationTrace) -> dict[str, Any]:
        """Return a plain dict representation of the trace."""
        return trace.as_dict()

    def summarize(self, trace: DelegationTrace) -> str:
        """One-line summary for tracking display."""
        alt_count = len(trace.alternatives_considered)
        tools_str = ", ".join(trace.tools_granted) if trace.tools_granted else "none"
        return (
            f"[{trace.trace_id[:8]}] run={trace.run_id!r} "
            f"goal={trace.goal_summary!r} → worker={trace.chosen_worker_id!r} "
            f"({alt_count} alt(s) considered, tools=[{tools_str}])"
        )
