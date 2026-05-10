"""TraceBundle v2 and model call trace with privacy controls.

EW-T049: TraceBundle v2 — full execution metadata, produced for all outcomes.
EW-T050: ModelCallTrace — provider/model/tokens/latency, raw prompt logging disabled.
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ── Outcome vocabulary ────────────────────────────────────────────────────────

class ExecutionOutcome(str, Enum):
    success = "success"
    denial = "denial"
    failure = "failure"
    timeout = "timeout"
    cancellation = "cancellation"
    invalid_request = "invalid_request"
    partial_success = "partial_success"
    degraded = "degraded"


# ── ModelCallTrace (EW-T050) ──────────────────────────────────────────────────

class ModelCallTrace(BaseModel):
    """Records one model call without exposing raw prompt/response. EW-T050.

    Raw prompt/response logging is disabled by default.
    Debug logging is scope-limited and redacted when enabled.
    """
    call_id: str
    provider_id: str
    model: str
    local_or_cloud: str    # "local" or "cloud"
    prompt_token_estimate: int = 0
    completion_token_estimate: int = 0
    latency_ms: float | None = None
    retry_count: int = 0
    fallback_used: bool = False
    fallback_provider: str | None = None
    outcome: str = "success"
    reason_code: str | None = None
    # Raw prompt/response: never logged unless debug_logging explicitly enabled
    _raw_prompt: str = ""       # NOT serialized
    _raw_response: str = ""     # NOT serialized

    def model_post_init(self, __context: Any) -> None:
        pass

    def as_trace_event(self) -> dict[str, Any]:
        """Safe representation for TraceBundle — no raw content. EW-T050."""
        return {
            "call_id": self.call_id,
            "provider_id": self.provider_id,
            "model": self.model,
            "local_or_cloud": self.local_or_cloud,
            "prompt_token_estimate": self.prompt_token_estimate,
            "completion_token_estimate": self.completion_token_estimate,
            "latency_ms": self.latency_ms,
            "retry_count": self.retry_count,
            "fallback_used": self.fallback_used,
            "fallback_provider": self.fallback_provider,
            "outcome": self.outcome,
            "reason_code": self.reason_code,
        }

    def as_debug_event(
        self,
        *,
        scope: str = "",
        redact_fn: Any = None,
    ) -> dict[str, Any]:
        """Debug representation with optionally redacted prompt/response. EW-T050.

        scope must be explicitly provided — empty scope → no debug output.
        """
        if not scope:
            return self.as_trace_event()

        from worker.core.sanitizer import sanitize
        prompt = (redact_fn or sanitize)(self._raw_prompt).text if self._raw_prompt else ""
        response = (redact_fn or sanitize)(self._raw_response).text if self._raw_response else ""

        base = self.as_trace_event()
        base["debug_scope"] = scope
        base["prompt_preview"] = prompt[:200] if prompt else ""
        base["response_preview"] = response[:200] if response else ""
        return base


# ── TraceBundleV2 (EW-T049) ───────────────────────────────────────────────────

@dataclass
class TraceEventV2:
    ts: float = field(default_factory=time.time)
    event_type: str = ""
    reason_code: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ts": self.ts,
            "event_type": self.event_type,
            "reason_code": self.reason_code,
            "payload": self.payload,
        }


@dataclass
class TraceBundleV2:
    """Full execution trace. EW-T049.

    Produced for ALL outcomes: success, denial, failure, timeout, cancellation.
    Never contains raw secrets or sensitive payloads.
    """
    execution_id: str
    task_id: str
    goal_id: str | None
    actor_ref: str
    capability_hash: str
    context_hash: str
    model_id: str
    outcome: ExecutionOutcome
    events: list[TraceEventV2] = field(default_factory=list)
    model_calls: list[ModelCallTrace] = field(default_factory=list)
    skill_ids_used: list[str] = field(default_factory=list)
    subworker_ids: list[str] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    cancelled: bool = False
    timed_out: bool = False

    # ── Helpers ───────────────────────────────────────────────────────────────

    def append(
        self,
        event_type: str,
        *,
        reason_code: str | None = None,
        **payload: Any,
    ) -> None:
        self.events.append(TraceEventV2(
            event_type=event_type,
            reason_code=reason_code,
            payload=payload,
        ))

    def record_model_call(self, call: ModelCallTrace) -> None:
        self.model_calls.append(call)

    def finish(self, outcome: ExecutionOutcome) -> None:
        self.outcome = outcome
        self.finished_at = time.time()
        self.append(f"execution_{outcome.value}", reason_code=None)

    def cancel(self, reason: str = "cancelled") -> None:
        self.cancelled = True
        self.finish(ExecutionOutcome.cancellation)
        self.append("cancellation", reason_code=reason)

    def timeout(self, reason: str = "timeout") -> None:
        self.timed_out = True
        self.finish(ExecutionOutcome.timeout)
        self.append("timeout", reason_code=reason)

    # ── Serialization ─────────────────────────────────────────────────────────

    def as_dict(self, *, include_model_calls: bool = True) -> dict[str, Any]:
        """Safe serialization — no raw prompts/responses. EW-T049."""
        d: dict[str, Any] = {
            "execution_id": self.execution_id,
            "task_id": self.task_id,
            "goal_id": self.goal_id,
            "actor_ref": self.actor_ref,
            "capability_hash": self.capability_hash,
            "context_hash": self.context_hash,
            "model_id": self.model_id,
            "outcome": self.outcome.value,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "cancelled": self.cancelled,
            "timed_out": self.timed_out,
            "event_count": len(self.events),
            "skill_ids_used": self.skill_ids_used,
            "subworker_ids": self.subworker_ids,
            "events": [e.as_dict() for e in self.events],
        }
        if include_model_calls:
            d["model_calls"] = [c.as_trace_event() for c in self.model_calls]
        return d

    # ── Factory ───────────────────────────────────────────────────────────────

    @classmethod
    def from_envelope(
        cls,
        envelope: "ExecutionEnvelope",  # type: ignore[name-defined]
        *,
        model_id: str = "",
        context_hash: str = "",
    ) -> "TraceBundleV2":
        from worker.core.execution_envelope import ExecutionEnvelope
        return cls(
            execution_id=envelope.audit_correlation_id,
            task_id=envelope.task_id,
            goal_id=envelope.goal_id,
            actor_ref=envelope.actor_ref,
            capability_hash=envelope.capability_grant.snapshot_hash,
            context_hash=context_hash,
            model_id=model_id,
            outcome=ExecutionOutcome.success,  # updated on finish()
        )
