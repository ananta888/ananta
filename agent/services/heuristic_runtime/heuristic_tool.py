"""HeuristicTool — registers 'select_heuristic' as a callable worker tool.

Tool execution is deterministic (via HeuristicRegistry) — no LLM free-text.
Tool calls are logged to DecisionTrace.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from agent.repositories.decision_trace_repo import DecisionTraceRepository
from agent.services.heuristic_runtime.decision_context import DecisionContext
from agent.services.heuristic_runtime.decision_trace import DecisionTrace
from agent.services.heuristic_runtime.heuristic_registry_service import HeuristicRegistry, get_heuristic_registry
from agent.services.heuristic_runtime.strategy import decide_for_context


TOOL_NAME = "select_heuristic"
TOOL_VERSION = "1.0.0"


@dataclass
class HeuristicToolCall:
    domain: str
    context_hash: str
    preferred_heuristic_id: str | None = None
    call_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": TOOL_NAME,
            "call_id": self.call_id,
            "domain": self.domain,
            "context_hash": self.context_hash,
            "preferred_heuristic_id": self.preferred_heuristic_id,
        }


@dataclass
class HeuristicToolResult:
    call_id: str
    heuristic_id: str | None
    version: str | None
    action_kind: str
    confidence: float
    ttl_seconds: float
    reason_codes: list[str]
    trace_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": TOOL_NAME,
            "call_id": self.call_id,
            "heuristic_id": self.heuristic_id,
            "version": self.version,
            "action_kind": self.action_kind,
            "confidence": self.confidence,
            "ttl_seconds": self.ttl_seconds,
            "reason_codes": list(self.reason_codes),
            "trace_id": self.trace_id,
        }


@dataclass
class HeuristicToolDefinition:
    """Describes the tool to the worker so it knows what options are available."""
    name: str
    description: str
    available_heuristics: list[dict[str, Any]]
    parameters: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "available_heuristics": self.available_heuristics,
            "parameters": self.parameters,
        }


class HeuristicTool:
    def __init__(
        self,
        registry: HeuristicRegistry | None = None,
        trace_repo: DecisionTraceRepository | None = None,
    ) -> None:
        self._registry = registry or get_heuristic_registry()
        self._trace_repo = trace_repo or DecisionTraceRepository()

    def get_definition(self, domain: str | None = None) -> HeuristicToolDefinition:
        """Return tool definition with descriptions from HeuristicDefinition.description."""
        if domain:
            heuristics = self._registry.get_active(domain)
        else:
            heuristics = self._registry.list_all()

        available = [
            {
                "heuristic_id": h.heuristic_id,
                "version": h.version,
                "domain": h.domain,
                "description": h.description,
                "strategy_kind": h.strategy_kind,
                "safety_class": h.safety_class,
            }
            for h in heuristics
        ]
        return HeuristicToolDefinition(
            name=TOOL_NAME,
            description=(
                "Select and activate a heuristic for the given domain. "
                "Execution is deterministic — no LLM inference. "
                "Returns action_kind, confidence, ttl_seconds, reason_codes."
            ),
            available_heuristics=available,
            parameters={
                "domain": {"type": "string", "required": True},
                "context_hash": {"type": "string", "required": True},
                "preferred_heuristic_id": {"type": "string", "required": False},
            },
        )

    def execute(self, call: HeuristicToolCall) -> HeuristicToolResult:
        """Execute the tool call deterministically via registry + strategy."""
        started_at = time.time()
        candidates = self._registry.get_active(call.domain)

        # Prefer explicitly requested heuristic
        if call.preferred_heuristic_id:
            pref = [h for h in candidates if h.heuristic_id == call.preferred_heuristic_id]
            if pref:
                candidates = pref

        ctx = DecisionContext(
            source_surface=call.domain,
            ai_status="offline",  # tool runs locally
        )
        result = decide_for_context(ctx, candidates)

        best_heuristic = candidates[0] if candidates else None

        # Log to DecisionTrace
        trace = DecisionTrace(
            surface=call.domain,
            context_hash=call.context_hash,
            heuristic_id=best_heuristic.heuristic_id if best_heuristic else None,
            strategy_id=result.strategy_id,
            confidence=result.confidence,
            source=result.source,
            action_kind=result.action_kind,
            reason_codes=list(result.reason_codes) + ["tool_call"],
            started_at=started_at,
        )
        trace.resolve()
        try:
            self._trace_repo.save(trace)
        except Exception:
            pass

        from agent.repositories.heuristic_lease_repo import _DOMAIN_TTL_DEFAULTS
        ttl = _DOMAIN_TTL_DEFAULTS.get(call.domain, {}).get("default", 7.0)

        return HeuristicToolResult(
            call_id=call.call_id,
            heuristic_id=best_heuristic.heuristic_id if best_heuristic else None,
            version=best_heuristic.version if best_heuristic else None,
            action_kind=result.action_kind,
            confidence=result.confidence,
            ttl_seconds=ttl,
            reason_codes=list(result.reason_codes),
            trace_id=trace.event_id,
        )
