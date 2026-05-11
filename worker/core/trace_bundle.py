"""TraceBundleV2: structured, secret-free execution trace for native worker. AWF-T037."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ProviderCallRecord:
    provider: str
    model: str | None = None
    request_hash: str | None = None   # hash of request — never raw prompt
    status: str = "ok"
    tokens_used: int = 0
    latency_ms: int = 0


@dataclass
class ToolCallRecord:
    tool_id: str
    status: str = "ok"
    output_chars: int = 0
    latency_ms: int = 0
    truncated: bool = False


@dataclass
class MemoryWriteRecord:
    memory_scope: str
    entry_type: str
    redacted: bool = True
    sensitivity: str = "internal"


@dataclass
class TraceBundleV2:
    """Secret-free structured execution trace. AWF-T037.

    Uses hashes/refs for prompts and artifacts — no raw secrets or unredacted prompts.
    Exists for success, denial, failure, timeout and cancellation.
    """
    execution_id: str
    task_id: str
    goal_id: str | None = None
    worker_profile: str = "balanced"
    capability_snapshot_hash: str = ""
    context_hash: str = ""
    policy_decision_ref: str = ""
    approval_refs: list[str] = field(default_factory=list)
    provider_calls: list[ProviderCallRecord] = field(default_factory=list)
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    memory_writes: list[MemoryWriteRecord] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)   # artifact_refs, not content
    warnings: list[str] = field(default_factory=list)
    degraded_states: list[dict[str, Any]] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    final_status: str = "pending"

    def finish(self, *, status: str) -> None:
        self.finished_at = time.time()
        self.final_status = status

    @property
    def duration_ms(self) -> int | None:
        if self.finished_at is None:
            return None
        return int((self.finished_at - self.started_at) * 1000)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": "trace_bundle.v2",
            "execution_id": self.execution_id,
            "task_id": self.task_id,
            "goal_id": self.goal_id,
            "worker_profile": self.worker_profile,
            "capability_snapshot_hash": self.capability_snapshot_hash,
            "context_hash": self.context_hash,
            "policy_decision_ref": self.policy_decision_ref,
            "approval_refs": list(self.approval_refs),
            "provider_calls": [
                {
                    "provider": p.provider,
                    "model": p.model,
                    "request_hash": p.request_hash,
                    "status": p.status,
                    "tokens_used": p.tokens_used,
                    "latency_ms": p.latency_ms,
                }
                for p in self.provider_calls
            ],
            "tool_calls": [
                {
                    "tool_id": t.tool_id,
                    "status": t.status,
                    "output_chars": t.output_chars,
                    "latency_ms": t.latency_ms,
                    "truncated": t.truncated,
                }
                for t in self.tool_calls
            ],
            "memory_writes": [
                {
                    "memory_scope": m.memory_scope,
                    "entry_type": m.entry_type,
                    "redacted": m.redacted,
                    "sensitivity": m.sensitivity,
                }
                for m in self.memory_writes
            ],
            "artifact_refs": list(self.artifacts),
            "warnings": list(self.warnings),
            "degraded_states": list(self.degraded_states),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_ms": self.duration_ms,
            "final_status": self.final_status,
        }
